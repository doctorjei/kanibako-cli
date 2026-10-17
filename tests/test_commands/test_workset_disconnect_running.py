"""``workset disconnect`` refuses a RUNNING member, and a plain one names its way back.

R7, measured on the base: with ``kb-<ws>-<box>`` running, ``disconnect <ws> <box>`` and
``disconnect <ws> <box> --remove-files`` both succeeded with ZERO runtime calls; the second
deleted the vaults and box dir under a live container, and the first orphaned the container
(no ref reaches it after).  The fix is the ONE running-box pre-flight every destructive verb
uses (``abort_if_box_running``), before the confirmation.  ``--force`` never overrides
it for a plain disconnect or a ``workset rm`` — both deregister a running box (171-RM Q1,
172-RF [Jei:2026-10-11]) — and still overrides it for ``--remove-files``, the purge.

The runtime is a real stub binary through ``KANIBAKO_DOCKER_CMD``, so the production
``_check_container_running`` asks it, as it would ask podman.  Every case goes through the
production parser, so a base run fails on behavior, not on an import.
"""

from __future__ import annotations

import fcntl
import os
import shlex

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths

FAKE_RUNTIME = """#!/bin/sh
# ps lists $FAKE_RUNNING as running; inspect knows them; nothing else exists.
echo "$*" >> "$FAKE_LOG"
case "$1" in
  ps) for n in $FAKE_RUNNING; do printf '%s\\tfake/rig:latest\\tUp 5 minutes\\n' "$n"; done; exit 0;;
  inspect) for n in $FAKE_RUNNING; do [ "$n" = "$2" ] && { echo '[{}]'; exit 0; }; done
           echo "Error: no such object: $2" >&2; exit 125;;
  *) exit 0;;
esac
"""


def _door(*argv: str) -> int:
  """A real verb, through the production parser."""
  from kanibako import cli

  args = cli.build_parser().parse_args(list(argv))
  return int(args.func(args) or 0)


@pytest.fixture
def world(tmp_home, config_file, tmp_path, monkeypatch):
  """Workset ``nws`` with external members ``bar`` and ``baz``, each holding vault data."""
  from kanibako.project.workset import add_project, create_workset

  std = load_std_paths(load_config(config_file))
  ws = create_workset("nws", tmp_home / "nws_root", std)
  for box in ("bar", "baz"):
    source = tmp_home / "src" / box
    source.mkdir(parents=True)
    add_project(ws, box, source, std)
    rw = ws.vault_rw_dir / box
    rw.mkdir(parents=True, exist_ok=True)
    (rw / "data.txt").write_text(f"KEEP-{box}\n")
  runtime = tmp_path / "fakepodman"
  runtime.write_text(FAKE_RUNTIME)
  runtime.chmod(0o755)
  log = tmp_path / "runtime.log"
  log.touch()
  monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(runtime))
  monkeypatch.setenv("FAKE_LOG", str(log))
  monkeypatch.setenv("FAKE_RUNNING", "")
  return std, ws, log


def _main(*argv: str) -> int:
  """A real verb through ``cli.main``: an escaping ``KanibakoError`` exits 1, named."""
  from kanibako import cli

  with pytest.raises(SystemExit) as done:
    cli.main(list(argv))
  return int(done.value.code or 0)


def _members(std, ws) -> list[str]:
  from kanibako.project.workset import load_workset

  return [p.name for p in load_workset(ws.root, ws.name, early_system=std.early_system).projects]


def _answer_yes(monkeypatch) -> None:
  monkeypatch.setattr("builtins.input", lambda: "yes")


class TestRunningMemberIsRefused:
  def test_plain_disconnect_of_a_running_member_is_refused(self, world, monkeypatch, capsys):
    std, ws, log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    assert _door("workset", "disconnect", "nws", "bar") == 1
    err = capsys.readouterr().err
    assert "its container is running (kb-nws-bar" in err
    assert "kanibako stop nws/bar" in err
    assert "bar" in _members(std, ws), "a refused disconnect dropped the member"
    assert "ps" in log.read_text(), "the runtime was never asked"

  def test_remove_files_on_a_running_member_deletes_nothing(self, world, monkeypatch, capsys):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-baz")
    assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 1
    assert "kanibako stop nws/baz" in capsys.readouterr().err
    assert (ws.vault_rw_dir / "baz" / "data.txt").read_text() == "KEEP-baz\n"
    assert (ws.projects_dir / "baz").is_dir()
    assert "baz" in _members(std, ws)

  def test_a_held_session_lock_with_no_container_is_refused(self, world, capsys):
    std, ws, _log = world
    lock = ws.projects_dir / "baz" / ".kanibako.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as held:
      fcntl.flock(held, fcntl.LOCK_EX)
      assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 1
    assert "a kanibako session holds its lock file" in capsys.readouterr().err
    assert (ws.vault_rw_dir / "baz" / "data.txt").is_file()
    assert "baz" in _members(std, ws)

  def test_force_does_not_disconnect_a_running_member(self, world, monkeypatch, capsys):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    assert _door("workset", "disconnect", "nws", "bar", "--force") == 1
    err = capsys.readouterr().err
    assert "Error: this box may be running" in err, err
    assert "kanibako stop nws/bar" in err and "--force" not in err, err
    assert "bar" in _members(std, ws)

  def test_force_still_overrides_the_guard_for_remove_files(self, world, monkeypatch, capsys):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    assert _door("workset", "disconnect", "nws", "bar", "--remove-files", "--force") == 0
    assert "Warning: this box may be running" in capsys.readouterr().err
    assert "bar" not in _members(std, ws)

  def test_a_running_neighbor_does_not_block_a_stopped_member(self, world, monkeypatch):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    _answer_yes(monkeypatch)
    assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 0
    assert not (ws.vault_rw_dir / "baz").exists()
    assert (ws.vault_rw_dir / "bar" / "data.txt").is_file()


def _registered(std) -> bool:
  from kanibako.project.workset import list_worksets

  return "nws" in list_worksets(std)


class TestWorksetRmWithRunningMembers:
  """``workset rm --force`` deregisters every member, so a running one refuses it."""

  @pytest.mark.parametrize("purge", [False, True])
  def test_force_does_not_remove_a_workset_with_a_running_member(
    self, world, monkeypatch, capsys, purge
  ):
    std, ws, log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar kb-nws-baz")
    argv = ["workset", "rm", "nws", "--force"] + (["--purge"] if purge else [])
    assert _door(*argv) == 1
    err = capsys.readouterr().err
    assert "kanibako stop nws/bar" in err and "kanibako stop nws/baz" in err, err
    assert "has 2 running member(s): bar, baz" in err, err
    assert "--force" not in err, err
    assert _registered(std) and _members(std, ws) == ["bar", "baz"]
    assert (ws.vault_rw_dir / "bar" / "data.txt").is_file()
    assert "ps" in log.read_text(), "the runtime was never asked"

  def test_one_running_member_is_enough_and_is_named(self, world, monkeypatch, capsys):
    std, _ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-baz")
    assert _door("workset", "rm", "nws", "--force") == 1
    err = capsys.readouterr().err
    assert "has 1 running member(s): baz." in err, err
    assert "kanibako stop nws/bar" not in err, err
    assert _registered(std)

  def test_a_held_session_lock_refuses_it(self, world, capsys):
    std, ws, _log = world
    lock = ws.projects_dir / "bar" / ".kanibako.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as held:
      fcntl.flock(held, fcntl.LOCK_EX)
      assert _door("workset", "rm", "nws", "--force") == 1
    assert "a kanibako session holds its lock file" in capsys.readouterr().err
    assert _registered(std)

  def test_an_unreadable_workset_file_still_asks_each_member(self, world, monkeypatch, capsys):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    (ws.root / "workset.yaml").write_text("{ not: [valid\n")
    assert _door("workset", "rm", "nws", "--force") == 1
    err = capsys.readouterr().err
    assert "running member(s): bar" in err, err
    assert _registered(std)

  def test_unreadable_membership_is_never_not_running(self, world, capsys):
    """Both files broken: no member can be asked, so --force is refused, with the cure."""
    from kanibako.errors import ConfigError

    std, ws, _log = world
    (ws.root / "workset.yaml").write_text("{ not: [valid\n")
    (ws.root / "registry.yaml").write_text("{ not: [valid\n")
    with pytest.raises(ConfigError) as refused:
      _door("workset", "rm", "nws", "--force")
    assert "registry.yaml" in str(refused.value)
    assert _registered(std)

  def test_stopped_members_are_removed_with_force(self, world, capsys):
    std, _ws, _log = world
    assert _door("workset", "rm", "nws", "--force") == 0
    assert not _registered(std)


def _hide_registry(ws, how: str) -> None:
  """Make the root registry.yaml a link no read can see through; keep the members."""
  reg = ws.root / "registry.yaml"
  reg.rename(ws.root / "registry.real")
  reg.symlink_to(reg if how == "loop" else ws.root / "gone.yaml")


class TestWorksetRmWithAnUnseenRegistry:
  """A registry.yaml link that resolves to nothing is unreadable, never "no members"."""

  @pytest.mark.parametrize("broken_settings", [False, True])
  @pytest.mark.parametrize("flags", [(), ("--force",), ("--purge",), ("--purge", "--force")])
  def test_rm_refuses_a_dangling_link_and_names_the_file(
    self, world, monkeypatch, capsys, broken_settings, flags
  ):
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    kept = ws.vault_rw_dir / "bar" / "data.txt"
    if broken_settings:
      (ws.root / "workset.yaml").write_text("{ not: [valid\n")
    _hide_registry(ws, "dangling")
    assert _door("workset", "rm", "nws", *flags) == 1
    err = capsys.readouterr().err
    assert f"{ws.root / 'registry.yaml'} cannot be read" in err, err
    assert "--force" not in err, err
    assert _registered(std) and kept.is_file()
    assert (ws.root / "registry.yaml").is_symlink()

  @pytest.mark.parametrize("broken_settings", [False, True])
  @pytest.mark.parametrize("flags", [(), ("--force",), ("--purge",), ("--purge", "--force")])
  def test_the_member_loader_refuses_a_looping_link(
    self, world, monkeypatch, capsys, broken_settings, flags
  ):
    """The loader's own ELOOP refusal answers first: rc 1, the file named, no --force."""
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    kept = ws.vault_rw_dir / "bar" / "data.txt"
    if broken_settings:
      (ws.root / "workset.yaml").write_text("{ not: [valid\n")
    _hide_registry(ws, "loop")
    assert _main("workset", "rm", "nws", *flags) == 1
    err = capsys.readouterr().err
    assert f"Error: {ws.root / 'registry.yaml'}: " in err, err
    assert "--force" not in err, err
    assert _registered(std) and kept.is_file()
    assert (ws.root / "registry.yaml").is_symlink()

  def test_a_stored_registry_null_checks_the_default_file(self, world, monkeypatch, capsys):
    """The standalone null reads members from the default path; that file is checked."""
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    (ws.root / "workset.yaml").write_text("workset:\n  registry: null\n")
    _hide_registry(ws, "loop")
    assert _main("workset", "rm", "nws", "--force") == 1
    err = capsys.readouterr().err
    assert f"Error: {ws.root / 'registry.yaml'}: " in err, err
    assert "--force" not in err, err
    assert _registered(std)

  def test_a_custom_registry_under_a_dangling_parent_is_refused(self, world, monkeypatch, capsys):
    """A ``workset.registry`` whose parent directory is a link to nothing: no members
    can be read there, so it refuses rather than reading "no members"."""
    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    kept = ws.vault_rw_dir / "bar" / "data.txt"
    (ws.root / "regdir").symlink_to(ws.root / "gone-dir")
    (ws.root / "workset.yaml").write_text(
      f"workset:\n  registry: {ws.root / 'regdir' / 'registry.yaml'}\n"
    )
    assert _door("workset", "rm", "nws", "--force") == 1
    err = capsys.readouterr().err
    assert f"{ws.root / 'regdir' / 'registry.yaml'} cannot be read" in err, err
    assert f"{ws.root / 'regdir'} is a link to nothing" in err, err
    assert "--force" not in err, err
    assert _registered(std) and kept.is_file()

  def test_an_unreadable_registry_file_still_fails_closed(self, world, monkeypatch):
    """EACCES on the file itself: the read raises, nothing is deregistered."""
    from kanibako.errors import ConfigError

    std, ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
    reg = ws.root / "registry.yaml"
    reg.chmod(0)
    try:
      with pytest.raises(ConfigError):
        _door("workset", "rm", "nws", "--force")
    finally:
      reg.chmod(0o644)
    assert _registered(std) and _members(std, ws) == ["bar", "baz"]


class TestStoppedMemberAndTheCure:
  def test_stopped_plain_disconnect_names_the_kept_files_and_cures(
    self, world, monkeypatch, capsys
  ):
    std, ws, _log = world
    _answer_yes(monkeypatch)
    assert _door("workset", "disconnect", "nws", "bar") == 0
    out = capsys.readouterr().out
    assert str(ws.vault_rw_dir / "bar") in out
    assert str(ws.projects_dir / "bar") in out
    assert "Re-adopt them: kanibako workset connect nws " in out
    assert "kanibako workset disconnect nws bar --remove-files" in out
    assert "bar" not in _members(std, ws)

  def test_the_printed_readopt_cure_restores_the_member_and_its_data(
    self, world, monkeypatch, capsys
  ):
    std, ws, _log = world
    _answer_yes(monkeypatch)
    assert _door("workset", "disconnect", "nws", "bar") == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "Re-adopt them:" in ln)
    argv = shlex.split(line.split("Re-adopt them:", 1)[1])
    assert argv[:3] == ["kanibako", "workset", "connect"]
    assert _door(*argv[1:]) == 0
    assert "bar" in _members(std, ws)
    assert (ws.vault_rw_dir / "bar" / "data.txt").read_text() == "KEEP-bar\n"

  def test_remove_files_on_a_stopped_member_prints_no_cure(self, world, monkeypatch, capsys):
    _std, ws, _log = world
    _answer_yes(monkeypatch)
    assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 0
    assert "Re-adopt" not in capsys.readouterr().out
    assert not (ws.vault_rw_dir / "baz").exists()

  def test_a_non_member_is_refused_as_before_without_a_running_warning(
    self, world, monkeypatch, capsys
  ):
    _std, _ws, _log = world
    monkeypatch.setenv("FAKE_RUNNING", "kb-nws-nope")
    _answer_yes(monkeypatch)
    assert _door("workset", "disconnect", "nws", "nope") == 1
    err = capsys.readouterr().err
    assert "not found in workset 'nws'" in err
    assert "may be running" not in err


class TestLeftoversNeverFailADisconnect:
  """The member is released BEFORE the listing, so the listing must not raise.

  Measured on 37743612: an unreadable ``vault/rw`` made ``Path.exists`` raise
  PermissionError after the release — traceback, rc 1, no cures, member gone.
  """

  @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 dir")
  def test_an_unreadable_store_is_listed_as_unchecked(self, world, monkeypatch, capsys):
    std, ws, _log = world
    _answer_yes(monkeypatch)
    ws.vault_rw_dir.chmod(0o000)
    try:
      rc = _door("workset", "disconnect", "nws", "bar")
    finally:
      ws.vault_rw_dir.chmod(0o755)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{ws.vault_rw_dir / 'bar'}  (could not be checked:" in out
    assert str(ws.projects_dir / "bar") in out
    assert "Re-adopt them: kanibako workset connect nws " in out
    assert "bar" not in _members(std, ws)
    assert (ws.vault_rw_dir / "bar" / "data.txt").read_text() == "KEEP-bar\n"

  def test_an_unresolvable_vault_pair_still_prints_the_cures(self, world, monkeypatch, capsys):
    from kanibako.commands import workset_cmd
    from kanibako.errors import ConfigError
    from kanibako.project import workset as workset_mod

    std, ws, _log = world
    _answer_yes(monkeypatch)
    real_remove = workset_cmd.remove_project

    def remove_then_break(*args, **kwargs):
      proj = real_remove(*args, **kwargs)

      def refuse(*_a, **_k):
        raise ConfigError("workset.yaml could not be read")

      monkeypatch.setattr(workset_mod, "resolve_workset_vault_pair", refuse)
      return proj

    monkeypatch.setattr(workset_cmd, "remove_project", remove_then_break)
    assert _door("workset", "disconnect", "nws", "bar") == 0
    out = capsys.readouterr().out
    assert "could not be located: workset.yaml could not be read" in out
    assert "Re-adopt them: kanibako workset connect nws " in out
    assert "bar" not in _members(std, ws)


class TestTheQuestionGatesTheRemoval:
  """A declined ``workset rm``/``disconnect`` answers rc 2 with ``Aborted.``; nothing goes."""

  def test_a_declined_rm_removes_nothing(self, world, tmp_home, monkeypatch, capsys):
    from kanibako.project.workset import create_workset, list_worksets

    std, _ws, _log = world
    empty = create_workset("ews", tmp_home / "ews_root", std)
    monkeypatch.setattr("builtins.input", lambda: "no")
    assert _door("workset", "rm", "ews", "--purge") == 2
    assert capsys.readouterr().out.endswith("Type 'yes' to confirm: Aborted.\n")
    assert "ews" in list_worksets(std) and empty.root.is_dir()

  def test_a_declined_disconnect_removes_nothing(self, world, monkeypatch, capsys):
    std, ws, _log = world
    monkeypatch.setattr("builtins.input", lambda: "no")
    assert _door("workset", "disconnect", "nws", "bar", "--remove-files") == 2
    assert capsys.readouterr().out.endswith("Type 'yes' to confirm: Aborted.\n")
    assert _members(std, ws) == ["bar", "baz"]
    assert (ws.vault_rw_dir / "bar" / "data.txt").is_file()

  def test_an_unanswerable_question_removes_nothing(self, world, monkeypatch, capsys):
    std, ws, _log = world

    def _lost():
      raise RuntimeError("lost sys.stdin")

    monkeypatch.setattr("builtins.input", _lost)
    assert _door("workset", "disconnect", "nws", "bar", "--remove-files") == 2
    assert capsys.readouterr().out.endswith("Type 'yes' to confirm: \nAborted.\n")
    assert _members(std, ws) == ["bar", "baz"]
    assert (ws.vault_rw_dir / "bar" / "data.txt").is_file()

  def test_force_never_asks(self, world, monkeypatch):
    std, ws, _log = world

    def _asked():
      raise AssertionError("--force asked the question")

    monkeypatch.setattr("builtins.input", _asked)
    assert _door("workset", "disconnect", "nws", "bar", "--force") == 0
    assert _members(std, ws) == ["baz"]
