"""Every name-, path- and ref-operand of a printed cure is a shell word.

The retained-box pair's quoting is pinned in :mod:`tests.test_cure_quoting`.
This covers the REST of the family: the ``rig prep`` cures, the ``box set
--box`` shaping cure, the reserved-workset ``rm``/``mv``/``cd`` steps, the
``unrenderable_box_name_refusal`` convert and move cures, the ``box convert
--move`` / ``box move`` refusals in ``_lifecycle`` (whose ``_cure_ref`` operand
is pinned by pasting the refusal's own printed cure), the ``box info``
"clear it" cure, the retired-key ``box set`` subject, and the six
``podman unshare`` cleanup cures plus the ``stop`` lock-file cure.

These operands are reachable with shell metacharacters in them.  A box
created from a PATH is registered under that path's basename VERBATIM —
``kanibako box create 'q$(touch pwned)'`` exits 0 and leaves a primary box
named ``q$(touch pwned)`` — and ``rig add --name`` stores whatever it was
handed, with no name validation at all.  So the metacharacter half is pinned
as an EXECUTION: each printed command is pasted into a real ``/bin/sh`` whose
leading program is a stub recording the argv the SHELL built, and the
assertion is on that argv plus the marker file the injected command would
leave.  A string match cannot say whether the second command ran.

⚑ THE ``unshare`` cures carry a SPACE-bearing name too, and their claim is
the stronger one: an unquoted operand does not merely run the wrong text, it
becomes a SECOND operand of the same recursive removal.  Those are pinned
against a name holding a space as well as against ``_HOSTILE``.
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
    assert "kanibako box move /home/u/plainbox <new-path> --name <new-name>" in text# ---------------------------------------------------------------------------
# The retired-key subject, the _lifecycle ref, and the cleanup cures.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", _HOSTILE)
class TestTheRetiredKeySubjectIsQuoted:
    """``settings_assemble._cure_subject`` is the cure's required positional.

    ⚑ A real box name is a value the reader PASTS, so it must arrive as one
    shell word.  Its PLACEHOLDER branch is the opposite and is pinned by
    :func:`test_the_cure_subject_placeholder_is_left_unquoted` beside this class.
    """

    def test_the_printed_subject_pastes_to_one_argv(self, name, tmp_path):
        from kanibako.settings.settings_assemble import _retired_key_cure

        cure = _retired_key_cure("box.agent_name", level="box", value="claude",
                                 box_name=name)

        assert f"kanibako box set {shlex.quote(name)} pref.system.agent=claude" in cure
        _assert_inert(cure, ["box", "set", name, "pref.system.agent=claude"],
                      tmp_path / "paste")


def test_the_cure_subject_placeholder_is_left_unquoted():
    """The reader fills the placeholder in, so quoting it would be a regression.

    The counterpart of the value branch above: ``<box>``/``<workset>`` are text
    the reader replaces, and a quoted placeholder would print ``'<box>'`` —
    literally what the reader is then required to type.
    """
    from kanibako.settings.settings_assemble import _cure_subject

    assert _cure_subject("box", None) == "<box>"
    assert _cure_subject("workset", None) == "<workset>"
    # At workset scope a name is not the positional either, so the placeholder
    # wins over a name it would otherwise splice in.
    assert _cure_subject("workset", "any name") == "<workset>"


def test_a_plain_cure_subject_prints_bare():
    """``shlex.quote`` is a no-op for the ordinary box, so nothing else moves."""
    from kanibako.settings.settings_assemble import _cure_subject

    assert _cure_subject("box", "plainbox") == "plainbox"
    assert _cure_subject("box", "/data/boxes/plainbox") == "/data/boxes/plainbox"


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheCureRefIsQuoted:
    """``_lifecycle._cure_ref`` is the reference its refusals cure a box BY.

    Driven through the two refusals that print it — ``box convert … --move`` and
    the ``box move`` refusal — for a member whose NAME carries the metacharacter.
    No podman: both refusals fire before any runtime is touched.
    """

    def _refuse(self, name, tmp_home, *, convert):
        """Drive one in-tree-landing refusal for a member named *name*.

        Returns the refusal's stderr and the canonical leaf the move cure names.
        """
        from kanibako.commands.box import _lifecycle
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        config = load_config(user_config_file())
        std = load_std_paths(config)
        ws1 = create_workset("ws1", tmp_home / "ws1_root", std)
        ws2 = create_workset("ws2", tmp_home / "ws2_root", std)
        leaf = ws1.workspaces_dir / name
        leaf.mkdir(parents=True)
        (leaf / "file.txt").write_text("wsdata")
        add_project(ws1, name, leaf, std)
        stray = ws2.root / "stray"

        common = dict(force=True, to_default=False, to_standalone=False,
                      to_workset="ws2", name=None)
        # A bare `--move` converts successfully; the refusal needs `--move <path>`,
        # whose landing is the non-canonical in-tree path the guard rejects.
        args = (argparse.Namespace(old=str(leaf), move=str(stray), **common)
                if convert
                else argparse.Namespace(old=str(leaf), new=str(stray), **common))
        handler = _lifecycle.run_convert if convert else _lifecycle.run_move
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert handler(args) == 1
        text = err.getvalue()
        assert "Refusing to record" in text, text
        return text, ws2.workspaces_dir / name

    def test_the_convert_move_cure_pastes_to_one_argv(self, name, tmp_path,
                                                      tmp_home, config_file,
                                                      credentials_dir):
        text, _ = self._refuse(name, tmp_home, convert=True)

        assert f"kanibako box convert {shlex.quote(name)} --workset ws2 --move" in text
        _assert_inert(_backticked(_line(text, "kanibako box convert"), "kanibako box convert"),
                      ["box", "convert", name, "--workset", "ws2", "--move"],
                      tmp_path / "paste-convert")

    def test_the_move_cure_pastes_to_one_argv(self, name, tmp_path, tmp_home,
                                              config_file, credentials_dir):
        text, leaf = self._refuse(name, tmp_home, convert=False)

        _assert_inert(_backticked(_line(text, "kanibako box move"), "kanibako box move"),
                      ["box", "move", name, str(leaf), "--workset", "ws2"],
                      tmp_path / "paste-move")


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheBoxInfoClearItCureIsQuoted:
    """``box info``'s "clear it" cure, staged with a FAKE runtime.

    ⚑ The cure prints only when the runtime reports a container that HOLDS the
    name but is not running, so the stub answers ``ps`` with no rows and
    ``inspect`` with success — ``container_exists`` is exactly that exit status.
    No podman, and no container.
    """

    def test_the_printed_cure_pastes_to_one_argv(self, name, tmp_path, monkeypatch,
                                                 tmp_home, config_file,
                                                 credentials_dir):
        from kanibako.commands.box._parser import run_info
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        config = load_config(user_config_file())
        std = load_std_paths(config)
        ws = create_workset("ws", tmp_home / "ws_root", std)
        leaf = ws.workspaces_dir / name
        leaf.mkdir(parents=True)
        add_project(ws, name, leaf, std)

        stub = tmp_path / "runtime-stub"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            run_info(argparse.Namespace(path=str(leaf), box=None))
        printed = out.getvalue()
        assert "clear it: kanibako stop" in printed, printed
        _assert_inert(_pasteable(_line(printed, "clear it:"), "clear it:"),
                      ["stop", name], tmp_path / "paste")


#: A name holding a SPACE.  The cleanup cures' claim is the stronger one: an
#: unquoted operand does not merely run the wrong text, it becomes a SECOND
#: operand of the same recursive removal.  ``_HOSTILE`` does not exercise that.
_SPACED = "x ~"


_BACKTICKED = re.compile(r"`([^`]*)`")


def _backticked(line: str, verb: str) -> str:
    """The backticked command in *line* that starts with *verb*.

    ⚑ A refusal names the target leaf in PROSE before it names the cure, so the
    line carries several backticked spans and the first one is not the command.
    """
    for candidate in _BACKTICKED.findall(line):
        if candidate.strip().startswith(verb):
            return candidate.strip()
    raise AssertionError(f"no backticked {verb} in {line!r}")


def _pasteable(line: str, lead: str) -> str:
    """The command a printed line carries, with the prose around it removed.

    ⚑ A cure reaches the terminal inside a sentence — after ``Try:``, inside the
    backticks of ``Run `…```, or behind ``clear it:``.  None of that is what a
    reader copies, and pasting it verbatim would run the PROSE instead of the
    cure, so the line is cut at *lead* first.  This is why the reserved-workset
    steps are pasted whole: they are printed one per line with no prose.
    """
    assert lead in line, line
    return line.split(lead, 1)[1].strip()


class TestTheCleanupCuresAreQuoted:
    """The ``podman unshare`` cures and the ``stop`` lock-file cure.

    ⚑ Each operand is built from the box name, so it is pinned against a name
    holding a space: unquoted, ``…/boxes/x`` and ``~`` reach the shell as two
    operands, and the reader's shell expands the second into ``$HOME``.  A stub
    ``rm`` records the argv the SHELL built — that is the whole claim, and
    nothing here deletes anything.
    """

    @staticmethod
    def _operands(command: str, scratch: Path) -> "list[str]":
        """Paste *command* with a recording ``rm`` on PATH; return rm's argv."""
        stub_dir = scratch / "stubbin"
        stub_dir.mkdir(parents=True, exist_ok=True)
        dump = scratch / "rm.dump"
        for name in ("podman", "rm"):
            prog = stub_dir / name
            if name == "podman":
                body = '#!/bin/sh\n[ "$1" = unshare ] && shift\nexec "$@"\n'
            else:
                body = ("#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\" >> "
                        + shlex.quote(str(dump)) + "\ndone\n")
            prog.write_text(body)
            prog.chmod(0o755)
        subprocess.run(["/bin/sh", "-c", command], cwd=str(scratch),
                       env={"PATH": f"{stub_dir}:/usr/bin:/bin",
                            "HOME": str(scratch / "fakehome")},
                       capture_output=True, text=True, check=False)
        return [a for a in dump.read_bytes().decode().split("\0") if a]

    def test_the_warn_undeleted_cure_keeps_one_operand(self, tmp_path):
        """``clean._warn_undeleted`` — the builder that prints the warning."""
        from kanibako.commands.clean import _warn_undeleted

        target = tmp_path / "boxes" / _SPACED
        target.parent.mkdir(parents=True, exist_ok=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _warn_undeleted(target)
        printed = err.getvalue()
        cure = _pasteable(_line(printed, "Try: podman unshare"), "Try: ")

        assert f"podman unshare rm -rf {shlex.quote(str(target))}" in printed
        assert self._operands(cure, tmp_path / "scratch") == ["-rf", str(target)]

    def test_the_primary_teardown_cure_keeps_one_operand(self, tmp_path, monkeypatch,
                                                         tmp_home, config_file,
                                                         credentials_dir):
        """``_parser._teardown_primary_box``, with the removal forced to fail."""
        from kanibako.commands.box import _parser
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        monkeypatch.setattr(_parser, "_purge_dir", lambda target: False)
        std = load_std_paths(load_config(user_config_file()))
        metadata = tmp_path / "boxes" / _SPACED
        metadata.mkdir(parents=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            _parser._teardown_primary_box(std, "x ~", metadata)
        cure = _pasteable(_line(err.getvalue(), "Try: podman unshare"), "Try: ")

        assert f"podman unshare rm -rf {shlex.quote(str(metadata))}" in err.getvalue()
        assert self._operands(cure, tmp_path / "scratch") == ["-rf", str(metadata)]

    def test_the_stop_lock_cure_keeps_one_operand(self, tmp_path):
        """``stop._stop_one`` with a runtime that holds neither a container nor a box."""
        from unittest.mock import MagicMock, patch

        from kanibako.commands.stop import _stop_one

        rt = MagicMock()
        # The lock cure is the arm where the runtime STOPPED nothing and holds no
        # container — the state a stale lock file blocks.
        rt.stop.return_value = False
        rt.is_running.return_value = False
        rt.list_running.return_value = []
        rt.container_exists.return_value = False
        rt.inspect_env.return_value = None
        proj = MagicMock()
        proj.metadata_path = tmp_path / "boxes" / _SPACED
        lock = proj.metadata_path / ".kanibako.lock"
        out = io.StringIO()
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target", return_value=proj),
            contextlib.redirect_stdout(out),
        ):
            assert _stop_one(rt, project_dir=None) == 0
        printed = out.getvalue()
        cure = _line(printed, "rm ")

        assert f"rm {shlex.quote(str(lock))}" in printed
        assert self._operands(cure, tmp_path / "scratch") == [str(lock)]
    def test_the_disconnect_cure_keeps_one_operand(self, tmp_path, monkeypatch,
                                                   tmp_home, config_file,
                                                   credentials_dir):
        """``workset disconnect``'s OSError arm, whose operand is the member's dir."""
        from kanibako.commands import workset_cmd
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(user_config_file()))
        ws = create_workset("ws", tmp_home / "ws_root", std)
        member = tmp_home / "member"
        member.mkdir()
        add_project(ws, _SPACED, member, std)

        from unittest.mock import patch

        err = io.StringIO()
        with (
            patch("kanibako.commands.workset_cmd.remove_project",
                  side_effect=OSError("refused")),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = workset_cmd.run_disconnect(argparse.Namespace(
                workset="ws", project=_SPACED, box=None,
                force=True, remove_files=False))
        printed = err.getvalue()
        assert rc == 1 and "could not remove project" in printed, printed
        cure = _pasteable(_line(printed, "unshare"), "Try: ")

        assert self._operands(cure, tmp_path / "scratch") == [
            "-rf", str(ws.projects_dir / _SPACED)]
