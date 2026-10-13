"""The helper hub process that outlives ``kanibako start`` (his 184-S2 (a)).

The real process tests spawn ``python -m kanibako.channels.hub_daemon`` against a fake
container runtime: a shell script that answers ``inspect`` and ``ps`` from a state file,
so a test flips the box between running and gone.
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from kanibako.channels import hub_daemon
from kanibako.channels.helper_listener import HelperContext, HelperHub
from kanibako.channels.hub_daemon import HubAction, HubProcess, decide_hub, serve, stop_hub
from kanibako.runtime.container import ContainerRuntime
from kanibako.settings.paths import BoxMode
from tests.support.early import early_record

CONTAINER = "kb-primary-myapp"

_FAKE_RUNTIME = """#!/bin/sh
state=$(cat "{state}" 2>/dev/null)
case "$1" in
  inspect) [ "$state" = running ] && {{ echo true; exit 0; }}; exit 1 ;;
  ps) case "$*" in *name=*) [ -n "$state" ] && printf '%s\\t%s\\n' {name} "$state" ;; esac
      exit 0 ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def short_dir():
    """A run dir short enough for an AF_UNIX path (pytest's tmp_path can be too long)."""
    path = Path(tempfile.mkdtemp(prefix="kbhub", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def box(tmp_path, short_dir):
    """``(socket_path, context, state_file)`` for a box whose liveness a test sets."""
    state = tmp_path / "state"
    fake = tmp_path / "fake-runtime"
    fake.write_text(_FAKE_RUNTIME.format(state=state, name=CONTAINER))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    helpers_dir = tmp_path / "shell" / "helpers"
    helpers_dir.mkdir(parents=True)
    socket_path = short_dir / "myapp-primary" / "helper.sock"
    socket_path.parent.mkdir()
    ctx = HelperContext(
        runtime=ContainerRuntime(command=str(fake)),
        image="test:latest",
        container_name_segments=("primary", "myapp"),
        shell_path=tmp_path / "shell",
        helpers_dir=helpers_dir,
        socket_path=socket_path,
        early=early_record(tmp_path, mode=BoxMode.primary),
    )
    return socket_path, ctx, state


def _answers(path: Path) -> bool:
    """True when a hub takes a connection at *path* (the in-box probe's test)."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(2.0)
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


# --------------------------------------------------------------------------- #
# The pure decision.                                                           #
# --------------------------------------------------------------------------- #

class TestDecideHub:
    @pytest.mark.parametrize(("live", "seen", "waited", "listening", "action"), [
        (True, True, 0.0, True, HubAction.SERVE),
        (True, True, 0.0, False, HubAction.REBIND),
        # The box is not up yet: wait for it, within the grace.
        (False, False, 5.0, True, HubAction.SERVE),
        (False, False, 5.0, False, HubAction.REBIND),
        # Once seen, a box that is not live is gone.
        (False, True, 5.0, True, HubAction.EXIT),
        # A box that never came up within the grace is gone too.
        (False, False, 10.0, True, HubAction.EXIT),
    ])
    def test_matrix(self, live, seen, waited, listening, action):
        assert decide_hub(box_live=live, seen_live=seen, waited=waited, grace=10.0,
                          listening=listening) is action


class _FakeHub:
    def __init__(self, listening=(True,)):
        self._listening = list(listening)
        self.rebinds = 0

    def listening(self):
        return self._listening.pop(0) if len(self._listening) > 1 else self._listening[0]

    def rebind(self):
        self.rebinds += 1


class TestServe:
    def test_exits_once_a_seen_box_is_gone(self):
        lives = iter([False, True, True, False])
        hub = _FakeHub()
        serve(hub, is_live=lambda: next(lives), stop=threading.Event(), poll=0,
              grace=1000.0)
        assert hub.rebinds == 0

    def test_rebinds_when_the_socket_no_longer_reaches_it(self):
        lives = iter([True, True, False])
        hub = _FakeHub(listening=[False, True])
        serve(hub, is_live=lambda: next(lives), stop=threading.Event(), poll=0,
              grace=1000.0)
        assert hub.rebinds == 1

    def test_a_stop_request_ends_it(self):
        stop = threading.Event()
        stop.set()
        serve(_FakeHub(), is_live=lambda: True, stop=stop, poll=0, grace=1000.0)


# --------------------------------------------------------------------------- #
# The hub's socket in a bound DIRECTORY.                                       #
# --------------------------------------------------------------------------- #

class TestHubSocketInBoundDirectory:
    def test_a_rebound_socket_answers_at_the_same_path(self, box):
        """The box removed the socket (its directory is bound rw): the hub says it is
        unreachable, binds again, and answers at the same path, which a running box
        sees through the directory bind.  (Mutation: rebind closes and does not listen
        again -> RED.)"""
        socket_path, ctx, _state = box
        hub = HelperHub()
        hub.start(socket_path, ctx)
        try:
            assert hub.listening() and _answers(socket_path)
            socket_path.unlink()
            assert not hub.listening()
            hub.rebind()
            assert hub.listening()
            assert _answers(socket_path)
        finally:
            hub.stop()

    def test_stop_removes_its_socket(self, box):
        """(Mutation: drop the unlink -> a dead socket stays in the box -> RED.)"""
        socket_path, ctx, _state = box
        hub = HelperHub()
        hub.start(socket_path, ctx)
        hub.stop()
        assert not socket_path.exists() and not socket_path.is_symlink()

    def test_stop_leaves_an_entry_that_is_not_its_socket(self, box):
        socket_path, ctx, _state = box
        hub = HelperHub()
        hub.start(socket_path, ctx)
        socket_path.unlink()
        socket_path.write_text("planted\n")
        hub.stop()
        assert socket_path.read_text() == "planted\n"

    def test_a_planted_symlink_is_removed_never_followed(self, box, tmp_path):
        socket_path, ctx, _state = box
        target = tmp_path / "outside"
        target.write_text("keep\n")
        socket_path.symlink_to(target)
        hub = HelperHub()
        hub.start(socket_path, ctx)
        try:
            assert target.read_text() == "keep\n"
            assert socket_path.is_socket()
        finally:
            hub.stop()


# --------------------------------------------------------------------------- #
# The real process.                                                            #
# --------------------------------------------------------------------------- #

class TestHubProcess:
    def test_outlives_its_launcher_and_exits_when_the_box_is_gone(self, box):
        """The persistent-box row: the hub answers while the box runs, though nothing
        in this process serves it, and exits when the box goes, removing its socket and
        freeing its lock.  (Mutation: the daemon exits on its first not-live tick
        regardless of ``seen_live`` -> it never serves a box that comes up later -> RED.)"""
        socket_path, ctx, state = box
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        try:
            # Ready before the box exists (the hub starts first, then the box).
            assert _answers(socket_path)
            state.write_text("running")
            time.sleep(3.0)  # past one liveness poll (2 s)
            assert _answers(socket_path)
            state.write_text("")
            assert _wait(lambda: not socket_path.exists()), "socket left behind"
            assert _wait(lambda: hub.process.poll() is not None), "hub still running"
            assert stop_hub(socket_path, timeout=1.0)
        finally:
            hub.stop()

    def test_a_paused_box_keeps_its_hub(self, box):
        socket_path, ctx, state = box
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        try:
            state.write_text("running")
            time.sleep(3.0)
            state.write_text("paused")
            time.sleep(4.5)
            assert hub.process.poll() is None and _answers(socket_path)
        finally:
            hub.stop()

    def test_stop_ends_it_and_its_socket(self, box):
        """``kanibako stop`` and a launch that is over stop the hub at once."""
        socket_path, ctx, state = box
        state.write_text("running")
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        hub.stop()
        assert hub.process.wait(timeout=15) == 0
        assert not socket_path.exists()

    def test_a_new_hub_takes_over_from_a_stale_one(self, box):
        """A hub still holding the lock serves a box that is gone (a running box is
        reattached, never relaunched): the next launch's hub stops it and serves."""
        socket_path, ctx, state = box
        state.write_text("running")
        old, new = HubProcess(), HubProcess()
        assert old.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        try:
            assert new.start(socket_path, ctx, log_path=None, container=CONTAINER,
                             stderr_path=None)
            assert old.process.wait(timeout=15) == 0
            assert new.process.poll() is None and _answers(socket_path)
        finally:
            new.stop()
            old.stop()

    def test_its_warnings_reach_its_log(self, box, tmp_path):
        """A hub that cannot start says why in its log, and the launch hears no ready."""
        socket_path, ctx, _state = box
        # A directory where the socket goes: ``bind`` cannot make the socket there.
        socket_path.mkdir()
        (socket_path / "x").write_text("")
        log = tmp_path / "logs" / "myapp.helper-hub.log"
        hub = HubProcess()
        assert not hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                             stderr_path=log)
        assert hub.process.wait(timeout=15) != 0
        assert log.read_text().strip()

    def test_stop_signals_only_a_hub_process(self, box):
        """A pid in the lock that is not a hub is never signaled."""
        socket_path, _ctx, _state = box
        lock = hub_daemon.hub_lock_path(socket_path)
        lock.write_text(f"{os.getpid()}\n")
        assert hub_daemon._holder_pid(lock) is None


class TestHubProcessExitHook:
    """A ``start`` that returns between the spawn and the launch (a setup refusal, say)
    leaves no hub behind; one that hands the hub to a box that came up does."""

    def test_an_unreleased_hub_is_stopped_at_exit(self, box):
        """(Mutation: drop the exit hook -> the hub waits out its grace -> RED.)"""
        socket_path, ctx, _state = box
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        hub._at_exit()  # what interpreter exit runs
        assert hub.process.wait(timeout=15) == 0
        assert not socket_path.exists()

    def test_a_released_hub_survives_exit(self, box):
        socket_path, ctx, state = box
        state.write_text("running")
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        try:
            hub.release()
            hub._at_exit()
            time.sleep(0.5)
            assert hub.process.poll() is None and _answers(socket_path)
        finally:
            hub.stop()
