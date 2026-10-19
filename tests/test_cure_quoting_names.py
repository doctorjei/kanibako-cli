"""Every name-, path-, image- and ref-operand of a printed cure is a shell word.

The retained-box pair's quoting is pinned in :mod:`tests.test_cure_quoting`.
This file covers the rest of the family:

* ``rig prep``; ``box set --box``; the reserved-workset ``rm``/``mv``/``cd``
  steps; the ``unrenderable_box_name_refusal`` move cures; the
  retired-key ``box set`` subject;
* ``_lifecycle``: the ``box convert --move`` / ``box move`` refusals (the
  ``_cure_ref`` operand), the in-place-rename ``box move``, and the
  membership guard's ``--workset``;
* ``box info``'s "clear it" cure; ``start``'s ``--restart`` stop cure, its
  legacy-container stop cure, and its baseline ``run --rm -it <image> bash``
  hint; the orphaned-home ``ls``;
* ``workset connect``'s primary-box cures; ``paths``' named-box ``box show``
  and ``workset disconnect``; the retired-share ``workset share rm``/``add``;
* every ``podman unshare rm -rf`` cleanup cure (``clean``, both teardowns,
  ``duplicate``, ``extract``, ``workset disconnect``) and ``stop``'s lock-file
  ``rm``.

A box created from a PATH by an older kanibako is registered under that basename
VERBATIM, and ``rig add --name`` stores whatever it is handed, so these operands reach a
printed cure with shell metacharacters or a space in them.  Each cure is
therefore pinned as an EXECUTION: the printed command is pasted into a real
``/bin/sh`` whose leading program is a recording stub, and the assertions are
on the argv the SHELL built and on the marker file an injected command would
leave.  The operands are ``q;>pwned``/``q$(touch pwned)`` (a command runs)
and ``x ~`` (one operand becomes two, the second expanding to ``$HOME``),
and each class's parametrization names the ones it pastes.
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


def _paste(
  command: str, scratch: Path, stub: str = "kanibako"
) -> "tuple[list[str], Path, Path, str]":
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
    'for a in "$@"; do printf \'%s\\0\' "$a"; done > ' + shlex.quote(str(argv_dump)) + "\n"
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


def _assert_inert(
  command: str, expected_argv: "list[str]", scratch: Path, stub_cwd: Path | None = None
) -> None:
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
    for key in (
      "HOME",
      "XDG_CONFIG_HOME",
      "XDG_DATA_HOME",
      "XDG_STATE_HOME",
      "XDG_CACHE_HOME",
      "XDG_RUNTIME_DIR",
    )
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
    env=env,
    cwd=str(cwd),
    capture_output=True,
    text=True,
    timeout=300,
    check=False,
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

  def test_the_printed_rig_cure_pastes_to_one_argv(
    self,
    name,
    tmp_home,
    config_file,
    tmp_path,
    monkeypatch,
  ):
    # ``rig add`` refuses without a runtime but never calls it for a ref: an
    # executable stub, so this passes without podman.
    stub = tmp_path / "fake-podman"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
    printed = _printed(["rig", "add", "--name", name, "docker.io/library/alpine:3.19"])

    assert f"kanibako rig prep {shlex.quote(name)}" in printed
    # The cure is a WHOLE line; prose quotes around it would nest.
    assert "'kanibako rig" not in printed, printed
    operand = re.search(r"^  kanibako rig prep (.+)$", printed, re.M).group(1)
    _assert_inert(f"kanibako rig prep {operand}", ["rig", "prep", name], tmp_path / "paste")


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
      group = None
      project_path = root
      metadata_path = root
      shell_path = root / "home"
      metadata = root / "metadata"

    args = argparse.Namespace(recover=False, name=None, standalone=None, no_vault=True)
    message = _create_recovery_refusal(
      args,
      None,
      Probe(),
      already=False,
      pending={"name": name},
    )
    assert message is not None, "the door owed no refusal for a pending create"

    line = _line(message, "box set --box")
    _assert_inert(
      line,
      ["box", "set", "--box", name, "box.enable_vault=false"],
      tmp_path / "paste",
    )


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheUnrenderableRefusalCuresAreQuoted:
  """A box whose name renders none is cured by its PATH, never by the name.

  ⚑ ``<new-path>``/``<new-name>`` are the message's own literal placeholders —
  the user fills them in — and ``<`` is a REDIRECT, so a paste substitutes them
  for a plain word first; what is under test is the PATH operand beside them.
  """

  def test_the_standalone_move_cure_pastes_to_one_argv(self, name, tmp_path):
    from kanibako.utils import unrenderable_box_name_refusal

    path = Path("/w") / name
    text = unrenderable_box_name_refusal("-droste", "standalone", path, None)

    assert f"kanibako box move {shlex.quote(str(path))}" in text
    cure = _line(text, "kanibako box move").replace("<new-path>", "/w/moved")
    _assert_inert(cure, ["box", "move", str(path), "/w/moved"], tmp_path / "paste")

  def test_the_move_cure_pastes_to_one_argv(self, name, tmp_path):
    from kanibako.utils import unrenderable_box_name_refusal

    path = Path("/w") / name
    text = unrenderable_box_name_refusal("-droste", "primary", path, None)

    # The offered name is ``-droste``'s '_' spelling (spec §0).
    cure = _line(text, "kanibako box move").replace("<new-path>", "/w/moved")
    _assert_inert(
      cure,
      ["box", "move", str(path), "/w/moved", "--name", "_droste"],
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
    made = _cli(cli_env, "workset", "create", str(root), "--name", "staging", cwd=home)
    assert made.returncode == 0, made.stderr
    member = root.parent / "member"
    member.mkdir()
    connected = _cli(
      cli_env, "workset", "connect", "staging", str(member), "--name", "ext", cwd=home
    )
    assert connected.returncode == 0, connected.stderr

    registry = load_std_paths(load_config(user_config_file())).registry
    unregister_name(registry, "staging", section="worksets")
    register_name(registry, "primary", str(root), section="worksets")
    assert "primary" in dict(read_names(registry)["worksets"])

    refused = _cli(cli_env, "box", "info", cwd=member)
    assert refused.returncode == 1, refused.stdout
    assert "Working set 'primary' is registered under a reserved name" in refused.stderr

    steps = [
      ln.strip() for ln in refused.stderr.splitlines() if ln.strip().startswith(("mv ", "cd "))
    ]
    assert steps, refused.stderr
    for index, step in enumerate(steps):
      verb, operand, *rest = shlex.split(step)
      if verb == "cd":
        # ``cd`` is a builtin, so the stub records the CHAINED ``kanibako``
        # — ``rest`` is ``['&&', 'kanibako', <its args>]`` — and the stub's
        # own cwd is what proves the path arrived as one word.
        assert rest[:2] == ["&&", "kanibako"], step
        _assert_inert(step, rest[2:], tmp_path / f"paste-{index}", stub_cwd=Path(operand))
      else:
        _assert_inert(step, [verb, operand, *rest], tmp_path / f"paste-{index}")


def test_a_plain_operand_prints_the_same_bytes_it_did():
  """Quoting is a no-op for an operand that needs none."""
  from kanibako.utils import unrenderable_box_name_refusal

  text = unrenderable_box_name_refusal("-droste", "primary", Path("/home/u/plainbox"), None)
  assert "kanibako box move /home/u/plainbox <new-path> --name _droste" in text


# ---------------------------------------------------------------------------
# The retired-key subject, the _lifecycle ref, and the cleanup cures.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheRetiredKeySubjectIsQuoted:
  """``settings_assemble.cure_subject`` is the cure's required positional.

  ⚑ A real box name is a value the reader PASTS, so it must arrive as one
  shell word.  Its PLACEHOLDER branch is the opposite and is pinned by
  :func:`test_the_cure_subject_placeholder_is_left_unquoted` beside this class.
  """

  def test_the_printed_subject_pastes_to_one_argv(self, name, tmp_path):
    from kanibako.settings.settings_assemble import retired_key_cure

    # *box_ref* is ``paths.project_cure_ref``'s answer, already quoted: the cure
    # uses it as given, so it reaches the shell quoted EXACTLY once.
    cure = retired_key_cure(
      "box.agent_name", level="box", value="claude", box_ref=shlex.quote(name)
    )

    assert f"kanibako box set {shlex.quote(name)} pref.system.agent=claude" in cure
    _assert_inert(cure, ["box", "set", name, "pref.system.agent=claude"], tmp_path / "paste")


def test_the_cure_subject_placeholder_is_left_unquoted():
  """The reader fills the placeholder in, so quoting it would be a regression.

  The counterpart of the value branch above: ``<box>``/``<workset>`` are text
  the reader replaces, and a quoted placeholder would print ``'<box>'`` —
  literally what the reader is then required to type.
  """
  from kanibako.settings.settings_assemble import cure_subject

  assert cure_subject("box", None) == "<box>"
  assert cure_subject("workset", None) == "<workset>"
  # At workset scope a name is not the positional either, so the placeholder
  # wins over a name it would otherwise splice in.
  assert cure_subject("workset", "any name") == "<workset>"


# ---------------------------------------------------------------------------
# The retired-key subject REACHES the box from outside its working set.
# ---------------------------------------------------------------------------
# A named member's bare name resolves only inside its own path space (S8b), so a
# cure that printed it sent a reader in ``~`` to a refusal, or to a same-named
# primary box. The cure is RUN here, from outside, the way the reader pastes it.

_FIX = re.compile(r"^\s*Fix: (kanibako [^\n]+)$", re.MULTILINE)


def _member_with_retired(std, config, tmp_home, leaf_yaml: str):
  """A materialized ``wsx/bar`` member whose box file holds *leaf_yaml*."""
  from kanibako.project.workset import add_project, create_workset
  from kanibako.settings.paths import (
    WorksetSpec,
    box_workset_settings_paths,
    resolve_workset_project,
  )

  ws = create_workset("wsx", tmp_home / "worksets" / "wsx", std)
  source = tmp_home / "wsx-src"
  source.mkdir()
  add_project(ws, "bar", source)
  proj = resolve_workset_project(WorksetSpec.from_workset(ws), "bar", std, config, initialize=True)
  box_path, _ = box_workset_settings_paths(proj)
  box_path.parent.mkdir(parents=True, exist_ok=True)
  box_path.write_text(leaf_yaml)
  return proj, box_path


def _run_fix_from_outside(refusal: str, box_path: Path, tmp_home: Path, monkeypatch):
  """Delete the stored entry, then run the printed ``Fix:`` line from outside the set."""
  import yaml

  found = _FIX.search(refusal)
  assert found, refusal
  argv = shlex.split(found.group(1))
  box_path.write_text("")
  outside = tmp_home / "outside"
  outside.mkdir(exist_ok=True)
  monkeypatch.chdir(outside)
  assert _run(argv[1:]) == 0, found.group(1)
  return argv, yaml.safe_load(box_path.read_text()) or {}


def test_the_selection_cure_runs_from_outside_the_workset(
  std, config, tmp_home, credentials_dir, monkeypatch
):
  """``agent_select``'s retired ``box.agent_name`` cure names ``wsx/bar``, and it runs."""
  from kanibako.settings.agent_select import select_agent
  from kanibako.settings.settings_resolve import SettingsError

  proj, box_path = _member_with_retired(std, config, tmp_home, "box:\n  agent_name: claude\n")
  with pytest.raises(SettingsError) as refused:
    select_agent(std=std, proj=proj)

  argv, doc = _run_fix_from_outside(str(refused.value), box_path, tmp_home, monkeypatch)
  assert argv[:4] == ["kanibako", "box", "set", "wsx/bar"], argv
  assert doc["pref"]["system"]["agent"] == "claude", doc


@pytest.mark.writes_undeclared(
  "pref.agent.claude.auto_approve",
  reason="the retired leaf is parsed into the request store before the resolve "
  "refusal names it; the refusal and its cure are what this test runs.",
)
def test_the_resolve_cure_runs_from_outside_the_workset(
  std, config, tmp_home, credentials_dir, monkeypatch
):
  """The resolve seam's retired ``auto_approve`` cure names ``wsx/bar``, and it runs."""
  from kanibako.settings import settings_launch
  from kanibako.settings.settings_resolve import SettingsError

  proj, box_path = _member_with_retired(
    std, config, tmp_home, "pref:\n  agent:\n    claude:\n      auto_approve: true\n"
  )
  inputs = settings_launch.resolve_inputs(
    subject=settings_launch.ResolveSubject.BOX,
    std=std,
    proj=proj,
    agent_name="claude",
    system_path=std.settings,
  )
  with pytest.raises(SettingsError) as refused:
    settings_launch.build_launch_snapshot(
      **inputs.as_kwargs(), agent_name="claude", agent_path=None
    )

  argv, doc = _run_fix_from_outside(str(refused.value), box_path, tmp_home, monkeypatch)
  assert argv[:4] == ["kanibako", "box", "set", "wsx/bar"], argv
  assert doc["pref"]["agent"]["claude"]["access"] == "full", doc


def test_a_standalone_root_with_a_space_is_quoted_exactly_once(
  std, config, tmp_home, credentials_dir, monkeypatch
):
  """A standalone's cure subject is its ROOT; a space in it reaches the shell as ONE word."""
  from kanibako.settings.agent_select import select_agent
  from kanibako.settings.paths import (
    box_workset_settings_paths,
    establish_standalone,
    resolve_box_target,
  )
  from kanibako.settings.settings_resolve import SettingsError

  root = tmp_home / "my proj"
  root.mkdir()
  establish_standalone(std, root, enable_vault=False)
  proj = resolve_box_target(std, config, str(root), initialize=False)
  box_path, _ = box_workset_settings_paths(proj)
  box_path.parent.mkdir(parents=True, exist_ok=True)
  box_path.write_text("box:\n  agent_name: claude\n")
  with pytest.raises(SettingsError) as refused:
    select_agent(std=std, proj=proj)

  text = str(refused.value)
  assert f"box set {shlex.quote(str(root))} pref" in text, text
  argv, doc = _run_fix_from_outside(text, box_path, tmp_home, monkeypatch)
  assert argv[3] == str(root), argv
  assert doc["pref"]["system"]["agent"] == "claude", doc


@pytest.mark.parametrize("name", _HOSTILE)
def test_the_undropped_registry_row_cure_pastes_to_one_argv(name, tmp_path):
  """``_lifecycle._drop_registry_row``: a legacy row name reaches ``box rm`` as ONE word."""
  from kanibako.commands.box._lifecycle import _drop_registry_row

  def refuse():
    raise OSError("read-only")

  err = io.StringIO()
  with contextlib.redirect_stderr(err):
    _drop_registry_row(name, refuse)

  _assert_inert(
    _backticked(err.getvalue(), "kanibako box rm"), ["box", "rm", name], tmp_path / "paste"
  )


def test_a_plain_cure_subject_prints_bare():
  """``shlex.quote`` is a no-op for the ordinary box, so nothing else moves."""
  from kanibako.settings.settings_assemble import cure_subject

  assert cure_subject("box", "plainbox") == "plainbox"
  assert cure_subject("box", "/data/boxes/plainbox") == "/data/boxes/plainbox"


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

    common = dict(force=True, to_default=False, to_standalone=False, to_workset="ws2", name=None)
    # A bare `--move` converts successfully; the refusal needs `--move <path>`,
    # whose landing is the non-canonical in-tree path the guard rejects.
    args = (
      argparse.Namespace(old=str(leaf), move=str(stray), **common)
      if convert
      else argparse.Namespace(old=str(leaf), new=str(stray), **common)
    )
    handler = _lifecycle.run_convert if convert else _lifecycle.run_move
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      assert handler(args) == 1
    text = err.getvalue()
    assert "Refusing to record" in text, text
    return text, ws2.workspaces_dir / name

  def test_the_convert_move_cure_pastes_to_one_argv(
    self, name, tmp_path, tmp_home, config_file, credentials_dir
  ):
    text, _ = self._refuse(name, tmp_home, convert=True)

    # A named member's cure reference is ``<workset>/<box>`` (``_cure_ref``).
    assert f"kanibako box convert {shlex.quote('ws1/' + name)} --workset ws2 --move" in text
    _assert_inert(
      _line(text, "kanibako box convert"),
      ["box", "convert", "ws1/" + name, "--workset", "ws2", "--move"],
      tmp_path / "paste-convert",
    )

  def test_the_move_cure_pastes_to_one_argv(
    self, name, tmp_path, tmp_home, config_file, credentials_dir
  ):
    text, leaf = self._refuse(name, tmp_home, convert=False)

    _assert_inert(
      _line(text, "kanibako box move"),
      ["box", "move", "ws1/" + name, str(leaf), "--workset", "ws2"],
      tmp_path / "paste-move",
    )


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheBoxInfoClearItCureIsQuoted:
  """``box info``'s "clear it" cure, staged with a FAKE runtime.

  ⚑ The cure prints only when the runtime reports a container that HOLDS the
  name but is not running, so the stub answers ``ps`` with no rows and
  ``inspect`` with success — ``container_exists`` is exactly that exit status.
  No podman, and no container.
  """

  def test_the_printed_cure_pastes_to_one_argv(
    self, name, tmp_path, monkeypatch, tmp_home, config_file, credentials_dir
  ):
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
    # ⚑ The name rule (spec §0) makes a hostile name a LEGACY one, which renders
    # no container name — so there is no container to clear, and no cure to paste.
    assert "clear it: kanibako stop" not in printed, printed
    assert "kanibako stop" not in printed, printed


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
        body = (
          '#!/bin/sh\nfor a in "$@"; do printf \'%s\\0\' "$a" >> '
          + shlex.quote(str(dump))
          + "\ndone\n"
        )
      prog.write_text(body)
      prog.chmod(0o755)
    subprocess.run(
      ["/bin/sh", "-c", command],
      cwd=str(scratch),
      env={"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(scratch / "fakehome")},
      capture_output=True,
      text=True,
      check=False,
    )
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

  def test_the_primary_teardown_cure_keeps_one_operand(
    self, tmp_path, monkeypatch, tmp_home, config_file, credentials_dir
  ):
    """``_parser._run_purge_plan`` over a PRIMARY plan, with the removal forced to fail."""
    from kanibako.commands.box import _parser
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    monkeypatch.setattr("kanibako.runtime.container.remove_box_tree", lambda target: False)
    std = load_std_paths(load_config(user_config_file()))
    metadata = tmp_path / "boxes" / _SPACED
    metadata.mkdir(parents=True)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      _parser._run_purge_plan(_parser._primary_purge_plan(std, "x ~", metadata), std)
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

  def test_the_disconnect_cure_keeps_one_operand(
    self, tmp_path, monkeypatch, tmp_home, config_file, credentials_dir
  ):
    """``workset disconnect --remove-files``'s OSError arm, whose operand is the member's dir."""
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
      patch("kanibako.commands.workset_cmd.remove_project", side_effect=OSError("refused")),
      contextlib.redirect_stderr(err),
      contextlib.redirect_stdout(io.StringIO()),
    ):
      rc = workset_cmd.run_disconnect(
        argparse.Namespace(workset="ws", project=_SPACED, box=None, force=True, remove_files=True)
      )
    printed = err.getvalue()
    assert rc == 1 and "could not remove project" in printed, printed
    cure = _pasteable(_line(printed, "unshare"), "Try: ")

    assert self._operands(cure, tmp_path / "scratch") == ["-rf", str(ws.projects_dir / _SPACED)]


# ---------------------------------------------------------------------------
# The rest of the family, each pinned against a ``$( )`` name AND a spaced one.
# ---------------------------------------------------------------------------

#: The two operands every test below pastes: one that RUNS a command when left
#: bare, and one that SPLITS into two operands (the second expanding to $HOME).
_BOTH = ["q$(touch pwned)", _SPACED]


def _assert_unshare_inert(cure: str, target: Path, scratch: Path) -> None:
  """Pasting a ``podman unshare rm -rf`` *cure* removes exactly *target* and runs nothing."""
  operands = TestTheCleanupCuresAreQuoted._operands(cure, scratch)

  assert not (scratch / _MARKER).exists(), f"the pasted cure ran injected text: {cure}"
  assert operands == ["-rf", str(target)], f"the shell built {operands} from {cure}"


@pytest.mark.parametrize("name", _BOTH)
class TestTheRemainingCleanupCuresAreQuoted:
  """The ``podman unshare`` cures of standalone teardown, duplicate, and extract."""

  def test_the_standalone_teardown_cure_keeps_one_operand(
    self, name, tmp_path, monkeypatch, config_file
  ):
    """``_parser._run_purge_plan`` over a store step, with the removal forced to fail."""
    from kanibako.commands.box import _parser
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    # ⚑ The plan runner takes std: one step kind removes against the LIVE mold.
    std = load_std_paths(load_config(config_file))
    metadata = tmp_path / "boxes" / name
    metadata.mkdir(parents=True)
    monkeypatch.setattr("kanibako.runtime.container.remove_box_tree", lambda target: False)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
      assert (
        _parser._run_purge_plan(
          [_parser._PurgeStep("metadata", "box metadata", metadata, anchor=True)], std
        )
        is False
      )
    cure = _pasteable(_line(err.getvalue(), "Try: podman unshare"), "Try: ")

    _assert_unshare_inert(cure, metadata, tmp_path / "scratch")

  def test_the_duplicate_cure_keeps_one_operand(self, name, tmp_path, monkeypatch):
    """``_duplicate._duplicate_to_standalone --force`` over a store it cannot remove."""
    from unittest.mock import MagicMock

    from kanibako.commands.box import _duplicate
    from kanibako.errors import ProjectError

    store = tmp_path / "dst" / name
    store.mkdir(parents=True)
    monkeypatch.setattr(_duplicate, "_early_scope", lambda std, mode: None)
    monkeypatch.setattr(_duplicate, "remove_box_tree", lambda path: False)
    monkeypatch.setattr("kanibako.settings.paths.standalone_box_store", lambda root, early: store)
    with pytest.raises(ProjectError) as refused:
      _duplicate._duplicate_to_standalone(
        MagicMock(), tmp_path / "dst", MagicMock(), True, False, {}, register=False
      )
    cure = _pasteable(_line(str(refused.value), "Try: podman unshare"), "Try: ")

    _assert_unshare_inert(cure, store, tmp_path / "scratch")

  def test_the_extract_cure_keeps_one_operand(self, name, tmp_path, monkeypatch):
    """``restore._restore_one`` over box data it cannot remove."""
    import tarfile
    from unittest.mock import MagicMock

    from kanibako.commands import restore
    from kanibako.settings.paths import BoxMode

    staged = tmp_path / "staged" / "h"
    staged.mkdir(parents=True)
    (staged / "kanibako-archive-info.txt").write_text("Project path: /w/proj\n")
    archive = tmp_path / "box.txz"
    with tarfile.open(archive, "w:xz") as tar:
      tar.add(staged, arcname="h")

    metadata = tmp_path / "boxes" / name
    metadata.mkdir(parents=True)
    proj = MagicMock(project_hash="h", mode=BoxMode.primary, metadata_path=metadata)
    monkeypatch.setattr(restore, "resolve_any_project", lambda *a, **k: proj)
    monkeypatch.setattr(restore, "refuse_inherited_per_owner", lambda *a, **k: None)
    monkeypatch.setattr(restore, "workset_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(restore, "workset_name_token", lambda *a, **k: "t")
    monkeypatch.setattr(restore, "remove_box_tree", lambda path: False)
    monkeypatch.setattr("kanibako.commands.archive._recorded_workspace_of", lambda std, proj: None)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
      assert (
        restore._restore_one(
          MagicMock(boxes=tmp_path / "boxes"),
          MagicMock(),
          project_dir=None,
          archive_file=archive,
          force=False,
        )
        == 1
      )
    cure = _pasteable(_line(err.getvalue(), "Try: podman unshare"), "Try: ")

    _assert_unshare_inert(cure, metadata, tmp_path / "scratch")


@pytest.mark.parametrize("name", _BOTH)
class TestTheOtherPrintedCommandsAreQuoted:
  """Every other printed command whose operand is a name, a path, or an image."""

  def test_the_baseline_shell_hint_pastes_to_one_argv(self, name, tmp_path, monkeypatch):
    """``start._check_launch_baseline``'s ``run --rm -it <image> bash`` hint."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from kanibako.commands import start
    from kanibako.runtime import baseline

    monkeypatch.setattr(start, "probe_missing_executables", lambda *a: ["tmux"])
    monkeypatch.setattr(baseline, "executables", lambda: [])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      assert (
        start._check_launch_baseline(
          MagicMock(cmd="podman"),
          name,
          "tmux",
          "box1",
          SimpleNamespace(state=tmp_path / "state"),
          setting=None,
          cure_ref="box1",
          attached=True,
        )
        is start._BOOTSTRAP_MISSING
      )
    hint = _line(err.getvalue(), " run --rm -it ")

    argv, paste_cwd, _, _ = _paste(hint, tmp_path / "paste", stub="podman")
    assert not (paste_cwd / _MARKER).exists(), hint
    assert argv == ["run", "--rm", "-it", name, "bash"], argv

  def test_the_baseline_box_cures_paste_to_one_argv_each(self, name, tmp_path, monkeypatch):
    """``start._check_launch_baseline``'s ``kanibako shell`` + ``start --ephemeral`` cures,
    fed the box's :func:`project_cure_ref` as ``_run_container`` feeds it."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from kanibako.commands import start
    from kanibako.runtime import baseline
    from kanibako.settings.paths import BoxMode, box_cure_ref

    monkeypatch.setattr(start, "probe_missing_executables", lambda *a: ["tmux"])
    monkeypatch.setattr(baseline, "executables", lambda: [])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      assert (
        start._check_launch_baseline(
          MagicMock(cmd="podman"),
          "img",
          "tmux",
          "box1",
          SimpleNamespace(state=tmp_path / "state"),
          setting=None,
          cure_ref=box_cure_ref(BoxMode.primary, name, None, tmp_path),
          attached=True,
        )
        is start._BOOTSTRAP_MISSING
      )
    shell = _pasteable(_line(err.getvalue(), "kanibako shell "), "exists:")
    ephemeral = _line(err.getvalue(), " --ephemeral")

    for cure, want, sub in (
      (shell, ["shell", name], "s"),
      (ephemeral, ["start", name, "--ephemeral"], "e"),
    ):
      argv, paste_cwd, _, _ = _paste(cure, tmp_path / sub)
      assert not (paste_cwd / _MARKER).exists(), cure
      assert argv == want, argv

  def test_the_retired_share_cure_pastes_to_one_argv_each(self, name, tmp_path):
    """``workset_cmd._workset_raw_shares`` on a RETIRED name-keyed entry."""
    from kanibako.commands.workset_cmd import _workset_raw_shares
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.settings_assemble import ReadPurpose, read_settings_files
    from kanibako.settings.settings_resolve import SettingsError

    src = f"/host/{name}"
    path = tmp_path / "workset.yaml"
    dump_doc(path, {"workset": {"bindings": {"rw": {name: [src]}}}})
    files = read_settings_files((("workset", path),), purpose=ReadPurpose.NARROW)
    with pytest.raises(SettingsError) as refused:
      _workset_raw_shares(files)
    text = str(refused.value).replace("\n", " ")

    remove = _backticked(text, "kanibako workset share rm").replace("<workset>", "ws")
    add = (
      _backticked(text, "kanibako workset share add")
      .replace("<workset>", "ws")
      .replace("<box_dest>", "/dst")
    )
    _assert_inert(
      remove, ["workset", "share", "rm", "ws", name, "--mode", "rw"], tmp_path / "paste-rm"
    )
    _assert_inert(
      add, ["workset", "share", "add", "ws", f"{src}:/dst", "--mode", "rw"], tmp_path / "paste-add"
    )

  def test_the_restart_stop_cure_pastes_to_one_argv(self, name, tmp_path, start_mocks):
    """``_run_container --restart`` when the stop did not take."""
    from unittest.mock import patch

    from kanibako.commands.start import _run_container

    err = io.StringIO()
    with start_mocks() as m, patch("kanibako.commands.stop._stop_one", return_value=0):
      m.resolve_any_project.return_value.name = name
      m.runtime.is_running.return_value = True
      m.runtime.container_exists.return_value = True
      m.runtime.inspect_env.return_value = "claude"
      with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        assert (
          _run_container(
            project_dir=None,
            entrypoint=None,
            image_override=None,
            new_session=False,
            safe_mode=False,
            resume_mode=False,
            extra_args=[],
            persistent=True,
            restart=True,
          )
          == 1
        )
    # ⚑ Every name here is a LEGACY one under the name rule (spec §0): start refuses
    # it before any stop, so no stop cure prints at all.
    assert "has no container name" in err.getvalue(), err.getvalue()
    assert "could not stop" not in err.getvalue()

  def test_the_named_box_path_cures_paste_to_one_argv_each(self, name, tmp_path, monkeypatch):
    """``paths.check_workspace_not_named_box`` — ``box show`` and ``workset disconnect``."""
    from types import SimpleNamespace

    from kanibako.errors import ProjectError
    from kanibako.launch import box_resolve
    from kanibako.settings.paths import check_workspace_not_named_box

    monkeypatch.setattr(
      box_resolve,
      "find_connected_external_box",
      lambda path, std: SimpleNamespace(box_name=name, workset_name=name),
    )
    with pytest.raises(ProjectError) as refused:
      check_workspace_not_named_box(None, str(tmp_path / "w"))
    text = str(refused.value)

    assert "'kanibako" not in text, text
    _assert_inert(
      _line(text, "kanibako box show"), ["box", "show", f"{name}/{name}"], tmp_path / "paste-show"
    )
    _assert_inert(
      _line(text, "kanibako workset disconnect"),
      ["workset", "disconnect", name, name, "--force"],
      tmp_path / "paste-disconnect",
    )

  def test_the_connect_primary_box_cures_paste_to_one_argv_each(
    self, name, tmp_path, config_file, tmp_home, credentials_dir
  ):
    """``workset connect`` of a PRIMARY box's workspace: the convert/move/rm cures."""
    from kanibako.commands.workset_cmd import run_connect
    from kanibako.project.workset import create_workset
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import (
      load_std_paths,
      register_primary_box_name,
    )

    std = load_std_paths(load_config(config_file))
    ext = (tmp_home / "ext" / "member").resolve()
    ext.mkdir(parents=True)
    register_primary_box_name(std.primary_workset, name, str(ext))
    create_workset("xpb", (tmp_home / "ws_xpb").resolve(), std)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      assert (
        run_connect(
          argparse.Namespace(workset="xpb", source=str(ext), project_name="member", force=True)
        )
        == 1
      )
    text = err.getvalue()

    assert "'kanibako" not in text, text
    convert, renamed = (
      ln.strip() for ln in text.splitlines() if ln.startswith("  kanibako box convert ")
    )
    move, remove = _line(text, "kanibako box move"), _line(text, "kanibako box rm")
    _assert_inert(
      convert, ["box", "convert", name, "--workset", "xpb"], tmp_path / "paste-convert"
    )
    _assert_inert(
      renamed.replace("<member>", "mem"),
      ["box", "convert", name, "--workset", "xpb", "--name", "mem", "--move"],
      tmp_path / "paste-renamed",
    )
    _assert_inert(
      move.replace("<path>", "/w/p"), ["box", "move", name, "/w/p"], tmp_path / "paste-move"
    )
    _assert_inert(remove, ["box", "rm", name], tmp_path / "paste-rm")

  def test_the_in_tree_connect_refuses_the_leaf_name_before_its_cure(
    self, name, tmp_path, config_file, tmp_home, credentials_dir
  ):
    """In-tree, a leaf name that fails the box-name rule is refused; no cure carries it."""
    from kanibako.commands.workset_cmd import run_connect
    from kanibako.project.workset import create_workset, load_workset
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import (
      load_std_paths,
      register_primary_box_name,
    )

    std = load_std_paths(load_config(config_file))
    ws = create_workset("xpb", (tmp_home / "ws_xpb").resolve(), std)
    leaf = (ws.workspaces_dir / name).resolve()
    leaf.mkdir(parents=True)
    register_primary_box_name(std.primary_workset, "ownerbox", str(leaf))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
      assert (
        run_connect(
          argparse.Namespace(workset="xpb", source=str(leaf), project_name=name, force=True)
        )
        == 1
      )
    text = err.getvalue()

    assert f"Invalid box name '{name}'" in text
    assert "kanibako box convert" not in text
    assert [
      p.name for p in load_workset(ws.root, "xpb", early_system=std.early_system).projects
    ] == []

  def test_the_in_place_rename_cure_pastes_to_one_argv(self, name, tmp_path, monkeypatch):
    """``_lifecycle._default_rename_name`` refusing a primary box's in-place rename."""
    from unittest.mock import MagicMock

    from kanibako.commands.box import _lifecycle
    from kanibako.errors import ProjectError

    monkeypatch.setattr(_lifecycle, "_primary_name_at", lambda *a: name)
    with pytest.raises(ProjectError) as refused:
      _lifecycle._default_rename_name(MagicMock(), MagicMock(), tmp_path, name + "2")
    cure = _backticked(str(refused.value), "box move").replace("<new-path>", "/w/moved")

    argv, paste_cwd, _, _ = _paste(cure, tmp_path / "paste", stub="box")
    assert not (paste_cwd / _MARKER).exists(), cure
    assert argv == ["move", name, "/w/moved", "--name", name + "2"], argv

  def test_the_membership_guard_cure_names_one_workset(
    self, name, tmp_path, monkeypatch, config_file, tmp_home, credentials_dir
  ):
    """``_lifecycle._validate``'s refusal to land inside a workset it is not joining."""
    from kanibako.commands.box import _lifecycle
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths, resolve_project

    config = load_config(config_file)
    std = load_std_paths(config)
    source = tmp_home / "proj"
    source.mkdir()
    resolve_project(std, config, project_dir=str(source), initialize=True)
    foreign = tmp_home / "foreign"
    real_in_tree = _lifecycle.is_in_tree_workspace
    monkeypatch.setattr(_lifecycle, "list_worksets", lambda std: {name: foreign})
    monkeypatch.setattr(
      _lifecycle,
      "is_in_tree_workspace",
      lambda ws, path: ws.name == name or real_in_tree(ws, path),
    )
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
      assert (
        _lifecycle.run_move(
          argparse.Namespace(
            old=str(source),
            new=str(tmp_home / "dest"),
            force=True,
            to_default=False,
            to_standalone=False,
            to_workset=None,
            name=None,
          )
        )
        == 1
      )
    flag = _backticked(_line(err.getvalue(), "Use `--workset"), "--workset")

    _assert_inert(
      f"kanibako box move {flag}", ["box", "move", "--workset", name], tmp_path / "paste"
    )

  def test_the_orphaned_home_ls_cure_pastes_to_one_argv(self, name, tmp_path, monkeypatch):
    """``_parser._assert_primary_home_free_for_create`` over an unclaimed box home."""
    from types import SimpleNamespace

    from kanibako.commands.box._parser import _assert_primary_home_free_for_create
    from kanibako.errors import ProjectError

    (tmp_path / "boxes" / name).mkdir(parents=True)
    monkeypatch.setattr(
      "kanibako.project.registry_store.lookup_deregistered", lambda registry, box: None
    )
    monkeypatch.setattr("kanibako.launch.journal.pending_create", lambda journal, box_dir: None)
    std = SimpleNamespace(boxes=tmp_path / "boxes", registry=None, journal=None)
    with pytest.raises(ProjectError) as refused:
      _assert_primary_home_free_for_create(std, name)
    listing = _line(str(refused.value), "  ls ")

    argv, paste_cwd, _, _ = _paste(listing, tmp_path / "paste", stub="ls")
    assert not (paste_cwd / _MARKER).exists(), listing
    assert argv == [str(tmp_path / "boxes" / name)], argv

  def test_the_legacy_container_stop_cure_pastes_to_one_argv(self, name, tmp_path, monkeypatch):
    """``start._refuse_legacy_container``: the pre-``kb-`` name is built from the box name."""
    from unittest.mock import MagicMock

    from kanibako.commands import start

    legacy = f"kanibako-{name}"
    monkeypatch.setattr(start, "legacy_container_names", lambda proj: (legacy,))
    refusal = start._refuse_legacy_container(
      MagicMock(cmd="podman", is_running=lambda n: True), MagicMock()
    )

    argv, paste_cwd, _, _ = _paste(_line(refusal, " stop "), tmp_path / "paste", stub="podman")
    assert not (paste_cwd / _MARKER).exists(), refusal
    assert argv == ["stop", legacy], argv


# The settings refusals end on the read-back command, on its own line; a bind
# entry's name is free-form, so the operand that carries it is shell-quoted.
_SETTINGS_CURES = [
  pytest.param(
    lambda ck: ck.agent_node_bind_retired_error("agent.claude.bindings.ro.my dir", verb="set"),
    ["agent", "get", "claude", "bindings.ro.my dir"],
    id="agent_node_bind",
  ),
  pytest.param(
    lambda ck: ck.agent_node_bind_retired_error("agent.default.caches.x y", verb="set"),
    ["system", "get", "agent.default.caches"],
    id="agent_default_bind",
  ),
  pytest.param(
    lambda ck: ck.scope_bind_retired_error("box.caches.~/x y", verb="set"),
    ["box", "get", "<box>", "box.caches.~/x y"],
    id="scope_bind",
  ),
  pytest.param(
    lambda ck: ck.table_leaf_read_cure("transform_settings", "claude"),
    ["agent", "get", "claude", "transform_settings"],
    id="table_leaf",
  ),
  pytest.param(
    lambda ck: ck.agent_category_read_error("agent.claude.caches", "agent.claude.caches"),
    ["agent", "get", "claude", "caches"],
    id="agent_category",
  ),
  pytest.param(
    lambda ck: ck.foreign_scope_read_error("system.caches", "system.caches", ck.ConfigLevel.box),
    ["system", "get", "system.caches"],
    id="foreign_scope",
  ),
]


@pytest.mark.parametrize("produce,argv", _SETTINGS_CURES)
def test_a_settings_read_back_cure_is_a_whole_pasteable_line(produce, argv):
  from kanibako.settings import config_keys

  message = produce(config_keys)

  assert "'kanibako" not in message, message
  last = message.splitlines()[-1]
  assert last.startswith("  kanibako "), message
  assert shlex.split(last) == ["kanibako", *argv]


# ---------------------------------------------------------------------------
# A cure that names its box resolves from anywhere — the vault and purge doors.
# ---------------------------------------------------------------------------

_FOREIGN_TS = "20260101T000000Z"


def test_the_foreign_snapshot_cure_lists_the_named_member_from_anywhere(
  std, config, tmp_home, credentials_dir, monkeypatch, capsys
):
  """``box vault restore``'s foreign-snapshot cure lists ``wsx/bar``, not the cwd's box."""
  from kanibako.commands.vault_cmd import run_restore
  from kanibako.project.workset import add_project, create_workset
  from kanibako.settings.paths import WorksetSpec, resolve_workset_project
  from kanibako.snapshots import _versions_dir, create_snapshot

  ws = create_workset("wsx", tmp_home / "worksets" / "wsx", std)
  source = tmp_home / "wsx-src"
  source.mkdir()
  add_project(ws, "bar", source)
  proj = resolve_workset_project(WorksetSpec.from_workset(ws), "bar", std, config, initialize=True)
  assert proj.vault_rw_path is not None
  proj.vault_rw_path.mkdir(parents=True, exist_ok=True)
  (proj.vault_rw_path / "f.txt").write_text("bar's")
  own = create_snapshot(proj.vault_rw_path, box_name="bar")
  assert own is not None
  (_versions_dir(proj.vault_rw_path) / "other" / _FOREIGN_TS).mkdir(parents=True)

  outside = tmp_home / "outside"
  outside.mkdir()
  monkeypatch.chdir(outside)
  capsys.readouterr()
  rc = run_restore(argparse.Namespace(project="wsx/bar", name=_FOREIGN_TS, force=True))
  err = capsys.readouterr().err
  assert rc == 1, err
  cure = _line(err, "kanibako box vault list")
  assert cure == "kanibako box vault list wsx/bar", err

  listed = _printed(shlex.split(cure)[1:])
  assert own.name in listed, listed


@pytest.mark.parametrize("target", ["{home}/ghost", "{home}/my proj"])
def test_the_unresolved_purge_stop_cure_names_its_box(
  target, std, config, tmp_home, tmp_path, monkeypatch, capsys
):
  """``_refuse_purge_while_live``'s stop cure carries the box it could not resolve.

  Only a standalone's ROOT reaches the resolver (a primary is probed by its name), and
  a root reaches the box from any cwd; a bare ``kanibako stop`` acts on the cwd's box.
  """
  from kanibako.commands.box import _parser as P

  target = target.format(home=tmp_home)
  md = tmp_path / "md"
  md.mkdir()
  monkeypatch.setattr(
    P, "_resolve_purge_probe_target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
  )
  monkeypatch.setattr(
    P,
    "_check_container_running",
    lambda proj, fail_closed=True, container_name=None: (False, "down"),
  )
  capsys.readouterr()
  assert P._refuse_purge_while_live(std, config, target, md, False, "Purging this box")
  err = capsys.readouterr().err
  found = re.search(r"Stop the box first: (kanibako stop .*?), or pass --force", err)
  assert found, err
  _assert_inert(found.group(1), ["stop", target], tmp_path)


@pytest.mark.parametrize("force", [False, True])
def test_the_unresolved_purge_warning_offers_force_only_without_it(
  force, std, config, tmp_home, tmp_path, monkeypatch, capsys
):
  """An unresolved root with no lock warns; a run that passed --force is not told to."""
  from kanibako.commands.box import _parser as P

  md = tmp_path / "md"
  md.mkdir()
  monkeypatch.setattr(
    P, "_resolve_purge_probe_target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
  )
  capsys.readouterr()
  refused = P._refuse_purge_while_live(
    std, config, str(tmp_home / "ghost"), md, force, "Purging this box"
  )
  assert refused is not force
  err = capsys.readouterr().err
  assert "does not resolve, so its container was never asked" in err, err
  assert ("or pass --force" in err) is not force, err


def _three_boxes(std, config, tmp_home):
  """A primary ``bar``, a named member ``wsx/bar``, and a standalone rooted at ``my proj``."""
  from kanibako.project.workset import add_project, create_workset
  from kanibako.settings.paths import (
    WorksetSpec,
    establish_standalone,
    resolve_box_target,
    resolve_project,
    resolve_workset_project,
  )

  primary_src = tmp_home / "bar"
  primary_src.mkdir()
  primary = resolve_project(std, config, str(primary_src), initialize=True, name_override="bar")
  ws = create_workset("wsx", tmp_home / "worksets" / "wsx", std)
  member_src = tmp_home / "wsx-src"
  member_src.mkdir()
  add_project(ws, "bar", member_src)
  member = resolve_workset_project(
    WorksetSpec.from_workset(ws), "bar", std, config, initialize=True
  )
  root = tmp_home / "my proj"
  root.mkdir()
  establish_standalone(std, root, enable_vault=False)
  standalone = resolve_box_target(std, config, str(root), initialize=False)
  return {"primary": primary, "named": member, "standalone": standalone}


@pytest.mark.parametrize("kind", ["primary", "named", "standalone"])
def test_the_running_box_stop_cure_resolves_its_box_from_anywhere(
  kind, std, config, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys
):
  """``abort_if_box_running``'s stop cure names *proj* by a ref that resolves from ``~``.

  A primary ``bar`` and a named ``wsx/bar`` share a leaf name, so a ref that drops the
  workset lands on the wrong box; the standalone root has a space in it.
  """
  from kanibako.commands.box import _parser as P
  from kanibako.commands.box._lifecycle import abort_if_box_running
  from kanibako.settings.paths import resolve_box_target

  proj = _three_boxes(std, config, tmp_home)[kind]
  monkeypatch.setattr(
    P,
    "_check_container_running",
    lambda proj, fail_closed=True, container_name=None: (True, "running"),
  )
  capsys.readouterr()
  assert abort_if_box_running(proj, tmp_path / ".kanibako.lock", False, "It would hurt.")
  err = capsys.readouterr().err
  found = re.search(r"Stop the box first: (kanibako stop .*?), or pass --force", err)
  assert found, err
  argv = shlex.split(found.group(1))
  assert len(argv) == 3, argv

  outside = tmp_home / "outside"
  outside.mkdir()
  monkeypatch.chdir(outside)
  landed = resolve_box_target(std, config, argv[2], initialize=False)
  assert landed.metadata_path == proj.metadata_path, (argv, landed.metadata_path)


def test_a_box_with_no_paths_keeps_the_bare_stop_cure(tmp_path, capsys):
  """With no *proj* there is no ref; the lock file alone answers, and the cure is bare."""
  from kanibako.commands.box._lifecycle import abort_if_box_running

  lock = tmp_path / ".kanibako.lock"
  lock.write_text("")
  assert abort_if_box_running(None, lock, False, "It would hurt.")
  assert "Stop the box first (kanibako stop), or pass --force." in capsys.readouterr().err


@pytest.mark.parametrize("target", ["{home}/ghost", "{home}/my proj"])
def test_an_unresolved_purge_target_with_a_lock_names_it_in_the_stop_cure(
  target, std, config, tmp_home, tmp_path, monkeypatch, capsys
):
  """The lock-file answer for a standalone root that did not resolve carries that root."""
  from kanibako.commands.box import _parser as P

  target = target.format(home=tmp_home)
  md = tmp_path / "md"
  md.mkdir()
  (md / ".kanibako.lock").write_text("")
  monkeypatch.setattr(
    P, "_resolve_purge_probe_target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
  )
  capsys.readouterr()
  assert P._refuse_purge_while_live(std, config, target, md, False, "Purging this box")
  err = capsys.readouterr().err
  assert "a lock file exists" in err, err
  found = re.search(r"Stop the box first: (kanibako stop .*?), or pass --force", err)
  assert found, err
  _assert_inert(found.group(1), ["stop", target], tmp_path)
