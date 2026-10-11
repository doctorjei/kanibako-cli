"""``workset disconnect`` refuses a RUNNING member, and a plain one names its way back.

R7, measured on the base: with ``kb-<ws>-<box>`` running, ``disconnect <ws> <box>`` and
``disconnect <ws> <box> --remove-files`` both succeeded with ZERO runtime calls; the second
deleted the vaults and box dir under a live container, and the first orphaned the container
(no ref reaches it after).  The fix is the ONE running-box pre-flight every destructive verb
uses (``abort_if_box_running``), before the confirmation; ``--force`` overrides it.

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


def _members(std, ws) -> list[str]:
    from kanibako.project.workset import load_workset

    return [p.name for p in load_workset(ws.root, ws.name,
                                         early_system=std.early_system).projects]


def _answer_yes(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda: "yes")


class TestRunningMemberIsRefused:
    def test_plain_disconnect_of_a_running_member_is_refused(
            self, world, monkeypatch, capsys):
        std, ws, log = world
        monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
        assert _door("workset", "disconnect", "nws", "bar") == 1
        err = capsys.readouterr().err
        assert "its container is running (kb-nws-bar" in err
        assert "kanibako stop nws/bar" in err
        assert "bar" in _members(std, ws), "a refused disconnect dropped the member"
        assert "ps" in log.read_text(), "the runtime was never asked"

    def test_remove_files_on_a_running_member_deletes_nothing(
            self, world, monkeypatch, capsys):
        std, ws, _log = world
        monkeypatch.setenv("FAKE_RUNNING", "kb-nws-baz")
        assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 1
        assert "kanibako stop nws/baz" in capsys.readouterr().err
        assert (ws.vault_rw_dir / "baz" / "data.txt").read_text() == "KEEP-baz\n"
        assert (ws.projects_dir / "baz").is_dir()
        assert "baz" in _members(std, ws)

    def test_a_held_session_lock_with_no_container_is_refused(
            self, world, capsys):
        std, ws, _log = world
        lock = ws.projects_dir / "baz" / ".kanibako.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 1
        assert "a kanibako session holds its lock file" in capsys.readouterr().err
        assert (ws.vault_rw_dir / "baz" / "data.txt").is_file()
        assert "baz" in _members(std, ws)

    def test_force_overrides_the_guard_and_warns(self, world, monkeypatch, capsys):
        std, ws, _log = world
        monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
        assert _door("workset", "disconnect", "nws", "bar", "--force") == 0
        assert "Warning: this box may be running" in capsys.readouterr().err
        assert "bar" not in _members(std, ws)

    def test_a_running_neighbor_does_not_block_a_stopped_member(
            self, world, monkeypatch):
        std, ws, _log = world
        monkeypatch.setenv("FAKE_RUNNING", "kb-nws-bar")
        _answer_yes(monkeypatch)
        assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 0
        assert not (ws.vault_rw_dir / "baz").exists()
        assert (ws.vault_rw_dir / "bar" / "data.txt").is_file()


class TestStoppedMemberAndTheCure:
    def test_stopped_plain_disconnect_names_the_kept_files_and_cures(
            self, world, monkeypatch, capsys):
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
            self, world, monkeypatch, capsys):
        std, ws, _log = world
        _answer_yes(monkeypatch)
        assert _door("workset", "disconnect", "nws", "bar") == 0
        line = next(ln for ln in capsys.readouterr().out.splitlines()
                    if "Re-adopt them:" in ln)
        argv = shlex.split(line.split("Re-adopt them:", 1)[1])
        assert argv[:3] == ["kanibako", "workset", "connect"]
        assert _door(*argv[1:]) == 0
        assert "bar" in _members(std, ws)
        assert (ws.vault_rw_dir / "bar" / "data.txt").read_text() == "KEEP-bar\n"

    def test_remove_files_on_a_stopped_member_prints_no_cure(
            self, world, monkeypatch, capsys):
        _std, ws, _log = world
        _answer_yes(monkeypatch)
        assert _door("workset", "disconnect", "nws", "baz", "--remove-files") == 0
        assert "Re-adopt" not in capsys.readouterr().out
        assert not (ws.vault_rw_dir / "baz").exists()

    def test_a_non_member_is_refused_as_before_without_a_running_warning(
            self, world, monkeypatch, capsys):
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
    def test_an_unreadable_store_is_listed_as_unchecked(
            self, world, monkeypatch, capsys):
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

    def test_an_unresolvable_vault_pair_still_prints_the_cures(
            self, world, monkeypatch, capsys):
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
