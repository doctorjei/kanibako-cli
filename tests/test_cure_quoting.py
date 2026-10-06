"""Every operand of a printed cure is shell-quoted, because a cure is PASTED.

A box created from a PATH is registered under that path's basename VERBATIM:
``kanibako create 'q$(touch pwned)'`` exits 0 and leaves a primary box named
``q$(touch pwned)``, and ``box rm`` on it prints

    Restore it with 'kanibako box register q$(touch pwned)', ...

Pasted, the ``$(...)`` is a command substitution and runs.  The metacharacter
half is pinned as an EXECUTION: each printed command is pasted into a real
``/bin/sh`` whose leading program is a stub recording the argv the SHELL built,
and the assertion is on that argv plus the marker file the injected command would
leave.  A string match cannot say whether the second command ran.

The retained-box pair is rendered by one primitive, so the doors that share a
sentence print it identically and no door can drift into a second wording.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ⚑ HOSTILE OPERANDS ARE BASENAMES A REAL ``create`` PRODUCES.  ``q;>pwned`` needs
# the ``;``; the ``$( )`` form needs no separator, so the two fail differently.
_HOSTILE = ["q;>pwned", "q$(touch pwned)"]
_MARKER = "pwned"
_SAFE = "plainbox"
_SENTENCE = "Restore it with"

# The register half, delimited by whatever connective its message puts after the
# operand.  Non-greedy, so on a quoted operand it stops at the CLOSING quote
# ``shlex.quote`` adds rather than swallowing the pair.
_REGISTER_RE = re.compile(
    r"kanibako box register (.*?)'(?:, or delete it with | to recover it )", re.S,
)
_PURGE_RE = re.compile(r"kanibako box rm (.*?)\s*--purge", re.S)


def _pair(message: str) -> "tuple[str, str]":
    """The register and purge COMMANDS as *message* offers them.

    The message wraps each in prose single quotes, which are not part of what a
    user copies, so they are dropped; what is left is exactly the text printed
    between the verb and the connective that follows it.
    """
    register, purge = _REGISTER_RE.search(message), _PURGE_RE.search(message)
    assert register is not None, f"no register cure in {message!r}"
    assert purge is not None, f"no purge cure in {message!r}"
    return (
        f"kanibako box register {register.group(1)}",
        f"kanibako box rm {purge.group(1)} --purge",
    )


def _paste(command: str, scratch: Path, stub: str = "kanibako") -> "tuple[list[str], Path, str]":
    """Paste *command* into a real shell; return the argv it built, the cwd, the HOME.

    *stub* shadows the pasted command's own leading program, so what is under
    test is the ARGV THE SHELL BUILT and not what kanibako then does with it.
    The child environment is passed whole, so it cannot inherit this process's
    HOME, and the recorded value says whether it did.
    """
    stub_dir = scratch / "stubbin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    argv_dump = scratch / "argv.dump"
    home_dump = scratch / "home.dump"
    argv_dump.write_bytes(b"")
    program = stub_dir / stub
    program.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do printf '%s\\0' \"$a\"; done > " + str(argv_dump) + "\n"
        "printf '%s' \"$HOME\" > " + str(home_dump) + "\n"
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
    return (
        [a for a in argv_dump.read_bytes().decode().split("\0") if a],
        paste_cwd,
        home_dump.read_text(),
    )


def _paste_over_ssh(command: str, scratch: Path) -> "tuple[str, list[str], Path]":
    """Paste an ``ssh`` cure; return the destination and the REMOTE argv.

    The ``ssh`` stub behaves like ssh(1): it drops its options, ``--``, and the
    destination, then hands the remaining args, joined by spaces, to a shell —
    the remote re-parse a local-only quote does not survive.  The "remote"
    ``kanibako`` records the argv that second shell built.
    """
    stub_dir = scratch / "stubbin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    dest_dump = scratch / "dest.dump"
    argv_dump = scratch / "argv.dump"
    argv_dump.write_bytes(b"")
    ssh = stub_dir / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --) shift; break ;;\n"
        "    -[BbcDEeFIiJLlmOoPpQRSWw]) shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "printf '%s' \"$1\" > " + shlex.quote(str(dest_dump)) + "\n"
        "shift\n"
        "exec /bin/sh -c \"$*\"\n"
    )
    ssh.chmod(0o755)
    kanibako = stub_dir / "kanibako"
    kanibako.write_text(
        "#!/bin/sh\n"
        "{ printf '%s\\0' kanibako; for a in \"$@\"; do printf '%s\\0' \"$a\"; done; } > "
        + shlex.quote(str(argv_dump)) + "\n"
    )
    kanibako.chmod(0o755)

    paste_cwd = scratch / "pastecwd"
    paste_cwd.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=str(paste_cwd),
        env={"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(scratch)},
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        dest_dump.read_text() if dest_dump.exists() else "",
        [a for a in argv_dump.read_bytes().decode().split("\0") if a],
        paste_cwd,
    )


# ---------------------------------------------------------------------------
# Door drivers — the shipped parser and the real handlers, on a real std.
# ---------------------------------------------------------------------------

def _run(argv: "list[str]") -> int:
    """Run *argv* through the SHIPPED parser and handler; return its rc."""
    from kanibako.cli import build_parser

    parsed = build_parser().parse_args(argv)
    return parsed.func(parsed)


def _park_primary_box(name: str) -> Path:
    """Create a PRIMARY box registered under *name*; return its workspace path."""
    path = Path.cwd() / name
    assert _run(["box", "create", str(path), "--no-vault"]) == 0
    return path


def _standalone_root(name: str) -> Path:
    """Create a real STANDALONE box, then key it in the registry as *name*.

    The registration is the real store function, so the key is written as given;
    ``create`` itself would sanitize it away, which is why the hostile half of
    this door's operands is not reachable through ``box create``.
    """
    from kanibako.project import registry_store
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    root = Path.cwd() / "sa_root"
    root.mkdir(parents=True, exist_ok=True)
    assert _run(["box", "create", str(root), "--standalone", "--no-vault"]) == 0
    std = load_std_paths(load_config(user_config_file()))
    registry_store.register_standalone(std.registry, name, root)
    return root


# --- doors 1-4, the retained-box pair ---------------------------------------

def _at_rm_primary(name: str, capsys) -> str:
    """Door 4: the cure a ``box rm`` prints when it parks an ACTIVE primary box.

    Reached with no registry editing: ``create`` stores the basename verbatim and
    ``rm`` on the path resolves it back.
    """
    _park_primary_box(name)
    capsys.readouterr()
    assert _run(["box", "rm", str(Path.cwd() / name)]) == 0
    return capsys.readouterr().out


def _at_rm_standalone(name: str, capsys) -> str:
    """Door 3: the cure ``box rm <path>`` prints for a STANDALONE box."""
    root = _standalone_root(name)
    capsys.readouterr()
    assert _run(["box", "rm", str(root)]) == 0
    return capsys.readouterr().out


def _at_rm_deregistered(name: str, capsys) -> str:
    """Door 2: the cure printed for an entry that is ALREADY deregistered.

    ``box rm`` reaches this only for a NAME-route target, and
    :func:`validate_box_name` refuses every shell metacharacter, so the operand is
    handed to the handler directly; what is pinned is that the cure quotes
    whatever name it is given.
    """
    from kanibako.commands.box._parser import _purge_deregistered
    from kanibako.project import registry_store
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    std = load_std_paths(load_config(user_config_file()))
    metadata = std.boxes / name
    metadata.mkdir(parents=True, exist_ok=True)
    registry_store.register_deregistered(
        std.registry, name, kind="primary",
        workspace=str(metadata), metadata=str(metadata),
    )
    args = argparse.Namespace(target=name, box=None, purge=False, force=False)
    capsys.readouterr()
    rc = _purge_deregistered(
        std, name, dict(registry_store.lookup_deregistered(std.registry, name)), args,
    )
    assert rc == 0
    return capsys.readouterr().out


def _at_create_name_refusal(name: str, capsys) -> str:
    """Door 1: the refusal ``box create`` prints for retained metadata of *name*."""
    from kanibako.commands.box._parser import _assert_primary_home_free_for_create
    from kanibako.errors import ProjectError
    from kanibako.project import registry_store
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    std = load_std_paths(load_config(user_config_file()))
    metadata = std.boxes / name
    metadata.mkdir(parents=True, exist_ok=True)
    registry_store.register_deregistered(
        std.registry, name, kind="primary",
        workspace=str(metadata), metadata=str(metadata),
    )
    with pytest.raises(ProjectError) as raised:
        _assert_primary_home_free_for_create(std, name)
    return str(raised.value)


_PAIR_DOORS = [
    pytest.param(_at_rm_primary, id="rm_primary"),
    pytest.param(_at_rm_standalone, id="rm_standalone"),
    pytest.param(_at_rm_deregistered, id="rm_deregistered"),
    pytest.param(_at_create_name_refusal, id="create_orphan"),
]


def _printed_pair(door, name: str, capsys) -> "tuple[str, str]":
    return _pair(door(name, capsys))


@pytest.mark.parametrize("door", _PAIR_DOORS, ids=[p.values[0].__name__ for p in _PAIR_DOORS])
@pytest.mark.parametrize("name", _HOSTILE)
class TestTheRetainedBoxPairIsQuoted:
    """The register/purge pair, pasted, must not run the operand's own commands."""

    def test_the_printed_pair_carries_one_operand_per_command(
        self, door, name, tmp_home, config_file, credentials_dir, capsys
    ):
        """Each printed half names exactly the box, as ONE shell word.

        Pinned on the argv the shell builds, so an operand that split into two
        words cannot pass by both halves happening to exist.
        """
        register, purge = _printed_pair(door, name, capsys)

        assert shlex.split(register) == ["kanibako", "box", "register", name]
        assert shlex.split(purge) == ["kanibako", "box", "rm", name, "--purge"]

    def test_pasting_the_printed_pair_does_not_run_the_operand(
        self, door, name, tmp_home, config_file, credentials_dir, capsys, tmp_path
    ):
        """⭐ THE EXECUTION: pasting either half leaves no marker and one argv.

        BOTH halves are pasted, each into its own directory, so an operand that
        ran its own command would leave ``pwned`` beside it — and the ``--purge``
        half is included because it is the one that deletes.  The recorded HOME
        says the child inherited the throwaway one and not this session's.
        """
        register, purge = _printed_pair(door, name, capsys)

        for half, expected in (
            (register, ["box", "register", name]),
            (purge, ["box", "rm", name, "--purge"]),
        ):
            argv, paste_cwd, child_home = _paste(half, tmp_path / f"paste-{len(expected)}")

            assert not (paste_cwd / _MARKER).exists(), (
                f"the pasted cure ran injected text: {half}"
            )
            assert argv == expected
            assert child_home == str(tmp_path / f"paste-{len(expected)}" / "childhome")


def test_the_three_shared_doors_print_one_identical_sentence(
    tmp_home, config_file, credentials_dir, capsys
):
    """The three same-sentence doors are one primitive's output, not three copies.

    Only the cure SENTENCE is compared — each door prints a different lead-in
    before it, and it is the sentence that must not drift into a second wording.
    """
    def sentence(door, name) -> str:
        message = door(name, capsys)
        # From the sentence's own first word, so the different lead-in each door
        # prints before it is excluded without depending on where it wraps.
        return next(
            ln[ln.index(_SENTENCE):].strip()
            for ln in message.splitlines() if _SENTENCE in ln
        )

    spoken = {
        door.__name__: sentence(door, _HOSTILE[0])
        for door in (_at_rm_primary, _at_rm_standalone, _at_rm_deregistered)
    }
    assert len(set(spoken.values())) == 1, f"the pair sentence diverged: {spoken}"


class TestTheOtherTwoCuresAreQuoted:
    """The ``start`` and ``code --remote`` cures carry operands the CLI never validates."""

    @pytest.mark.parametrize(
        "designation", ["unregistered_box", "ws/box", "a\0b"],
        ids=["identifier", "qualified", "nul_bearing"],
    )
    def test_the_start_create_cure_quotes_the_operand_it_is_handed(self, designation):
        """This door is name-gated, so only a NUL-bearing operand needs quoting.

        The line is reached only when :func:`designation_route` is not PATH,
        which for a non-empty value is an IDENTIFIER, a ``<workset>/<box>`` pair
        of them, or a NUL-bearing INVALID — and :func:`is_valid_box_name`
        refuses whitespace and every shell metacharacter, so the first two are
        already quote-clean.  The NUL case is the one that still needs it, and it
        is pinned here so the branch cannot go back to printing its operand raw;
        a hostile ``$( )`` designation is a PATH and takes the quoted sibling.
        """
        from kanibako.commands.start import _no_box_error

        message = _no_box_error(designation)
        line = next(
            ln for ln in message.splitlines() if "Otherwise create a new box" in ln
        )

        assert line.strip() == (
            f"Otherwise create a new box:  kanibako create {shlex.quote(designation)}"
        )

    @pytest.mark.parametrize("name", _HOSTILE)
    def test_a_path_operand_takes_the_quoted_sibling(self, name, tmp_path):
        """A hostile operand is a PATH, so it reaches the line that already quoted."""
        from kanibako.commands.start import _no_box_error

        message = _no_box_error(name)
        cure = message.rsplit("To create a new box, run:", 1)[1].strip()

        assert shlex.split(cure) == ["kanibako", "create", name]

        argv, paste_cwd, _home = _paste(cure, tmp_path / "paste")
        assert not (paste_cwd / _MARKER).exists(), "the pasted cure ran injected text"
        assert argv == ["create", name]

    @pytest.mark.parametrize("box", [f"web$(touch {_MARKER})", "a b'c"])
    def test_the_remote_create_cure_survives_both_shells(self, box, tmp_home, capsys, tmp_path):
        """``--remote`` and ``--box`` are free-form; the box crosses TWO shells.

        ssh joins its trailing args with spaces and the REMOTE shell re-parses
        them, so the box needs a second quoting layer that a local-only quote
        lacks.  The pasted cure runs through an ``ssh`` stub that behaves like
        ssh(1) and a recording ``kanibako`` on the "remote" side.
        """
        from types import SimpleNamespace

        from kanibako.commands.code_cmd import _run_code_remote

        dest = f"myhost;>{_MARKER}"
        failed = SimpleNamespace(
            returncode=1, stderr="Error: no box at /home/u/webapp.", stdout="",
        )
        args = argparse.Namespace(project=None, box=box, remote=dest)
        with (
            patch("kanibako.commands.code_cmd._resolve_code_cli", return_value="/usr/bin/code"),
            patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/podman"),
            patch("kanibako.commands.code_cmd._wire_docker_path", return_value=None),
            patch("kanibako.vscode.vscode_remote.dispatch_wrapper_path", return_value=Path("/w")),
            patch("kanibako.vscode.vscode_remote.ensure_dispatch_wrapper"),
            patch("kanibako.vscode.vscode_remote.probe_remote", return_value=1000),
            patch("kanibako.vscode.vscode_remote.remote_context_name", return_value="ctx"),
            patch("kanibako.vscode.vscode_remote.tunnel_socket_path", return_value=Path("/t.sock")),
            patch("kanibako.vscode.vscode_remote.engine_url", return_value="unix:///t.sock"),
            patch("kanibako.vscode.vscode_remote.remote_socket_path", return_value="/run/x.sock"),
            patch("kanibako.vscode.vscode_remote.ensure_docker_context_meta"),
            patch("kanibako.vscode.vscode_remote.write_context_entry"),
            patch("kanibako.vscode.vscode_remote.ensure_tunnel"),
            patch("kanibako.vscode.vscode_remote.RemoteEngine", return_value=MagicMock()),
            patch("kanibako.vscode.vscode_remote.preflight_engine"),
            patch("kanibako.vscode.vscode_remote.remote_run_kanibako", return_value=failed),
        ):
            assert _run_code_remote(args, dest) == 1
        hint = next(
            ln for ln in capsys.readouterr().err.splitlines() if "Create it THERE" in ln
        )
        cure = hint.split("e.g.:", 1)[1].strip()

        remote_dest, remote_argv, paste_cwd = _paste_over_ssh(cure, tmp_path / "paste")
        assert not (paste_cwd / _MARKER).exists(), "the pasted cure ran injected text"
        assert remote_dest == dest
        assert remote_argv == ["kanibako", "create", box]


@pytest.mark.parametrize("name", [_SAFE, "q;>pwned"])
def test_a_plain_operand_prints_the_same_bytes_it_did(
    name, tmp_home, config_file, credentials_dir, capsys
):
    """Quoting is a no-op for an operand that needs none."""
    assert _pair(_at_rm_primary(name, capsys)) == (
        f"kanibako box register {shlex.quote(name)}",
        f"kanibako box rm {shlex.quote(name)} --purge",
    )