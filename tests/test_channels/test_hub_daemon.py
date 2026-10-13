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
fails=$(cat "{state}.fail" 2>/dev/null)
if [ -n "$fails" ] && [ "$fails" -gt 0 ]; then echo $((fails - 1)) > "{state}.fail"; exit 1; fi
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
    LIMIT = hub_daemon.MISS_LIMIT

    @pytest.mark.parametrize(("misses", "seen", "waited", "listening", "action"), [
        (0, True, 0.0, True, HubAction.SERVE),
        (0, True, 0.0, False, HubAction.REBIND),
        # The box is not up yet: wait for it, within the grace.
        (LIMIT, False, 5.0, True, HubAction.SERVE),
        (LIMIT, False, 5.0, False, HubAction.REBIND),
        # Once seen, fewer not-live reads in a row than the limit are a hiccup.
        (LIMIT - 1, True, 5.0, True, HubAction.SERVE),
        (LIMIT - 1, True, 5.0, False, HubAction.REBIND),
        # Once seen, the limit of not-live reads in a row means the box is gone.
        (LIMIT, True, 5.0, True, HubAction.EXIT),
        # A box that never came up within the grace is gone too.
        (LIMIT, False, 10.0, True, HubAction.EXIT),
        (LIMIT - 1, False, 10.0, True, HubAction.SERVE),
    ])
    def test_matrix(self, misses, seen, waited, listening, action):
        assert decide_hub(misses=misses, seen_live=seen, waited=waited, grace=10.0,
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
        lives = iter([False, True, True] + [False] * hub_daemon.MISS_LIMIT)
        hub = _FakeHub()
        serve(hub, is_live=lambda: next(lives), stop=threading.Event(), poll=0,
              grace=1000.0)
        assert hub.rebinds == 0

    def test_a_live_read_resets_the_misses(self):
        """Not-live reads that a live read interrupts never add up to an exit.
        (Mutation: misses not reset on a live read -> exits early -> RED.)"""
        short = [False] * (hub_daemon.MISS_LIMIT - 1)
        reads = [True] + short + [True] + short + [True]
        calls = []

        def is_live():
            calls.append(1)
            return reads[len(calls) - 1] if len(calls) <= len(reads) else False

        serve(_FakeHub(), is_live=is_live, stop=threading.Event(), poll=0, grace=1000.0)
        assert len(calls) == len(reads) + hub_daemon.MISS_LIMIT

    def test_rebinds_when_the_socket_no_longer_reaches_it(self):
        lives = iter([True, True] + [False] * hub_daemon.MISS_LIMIT)
        hub = _FakeHub(listening=[False, True])
        serve(hub, is_live=lambda: next(lives), stop=threading.Event(), poll=0,
              grace=1000.0)
        assert hub.rebinds == 1

    def test_a_stop_request_ends_it(self):
        stop = threading.Event()
        stop.set()
        serve(_FakeHub(), is_live=lambda: True, stop=stop, poll=0, grace=1000.0)


class TestBoxLiveProbe:
    def test_a_hung_runtime_call_is_a_miss_within_the_timeout(self, tmp_path, monkeypatch):
        """bifrost: ``podman inspect`` hung on the storage lock and the hub outlived its
        grace.  A call past :data:`PROBE_TIMEOUT` is killed and reads as not live.
        (Mutation: no timeout -> the probe waits out the whole hang -> RED.)"""
        hung = tmp_path / "hung-runtime"
        hung.write_text("#!/bin/sh\nexec sleep 5\n")
        hung.chmod(hung.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(hub_daemon, "PROBE_TIMEOUT", 0.5)
        began = time.monotonic()
        live = hub_daemon._box_live(ContainerRuntime(command=str(hung)), CONTAINER)
        assert live is False
        assert time.monotonic() - began < 3.0

    def test_a_slow_live_answer_still_reads_live(self, tmp_path):
        """A runtime that answers inside the timeout is read as before."""
        slow = tmp_path / "slow-runtime"
        slow.write_text("#!/bin/sh\nsleep 0.2\necho true\n")
        slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
        assert hub_daemon._box_live(ContainerRuntime(command=str(slow)), CONTAINER) is True


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

    def test_a_failing_probe_short_of_the_limit_keeps_its_hub(self, box):
        """D2: the runtime fails every call for ``MISS_LIMIT - 1`` ticks while the box
        runs (an engine hiccup); the hub, and so its helpers, keep serving.  (Mutation:
        exit on the first not-live read -> the hub stops its helpers -> RED.)"""
        socket_path, ctx, state = box
        state.write_text("running")
        hub = HubProcess()
        assert hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                         stderr_path=None)
        try:
            time.sleep(3.0)  # one live poll first: the box is seen
            # Each tick asks inspect, then ps; every call in these ticks fails.
            fail = Path(f"{state}.fail")
            fail.write_text(f"{2 * (hub_daemon.MISS_LIMIT - 1)}\n")
            assert _wait(lambda: (fail.read_text().strip() == "0"
                              or hub.process.poll() is not None), timeout=20.0)
            assert hub.process.poll() is None, "a probe hiccup stopped the hub"
            time.sleep(2.5)  # one more (live) tick
            assert hub.process.poll() is None and _answers(socket_path)
        finally:
            hub.stop()

    @pytest.mark.parametrize("empty_path_entry", [False, True])
    def test_imports_nothing_from_the_launch_directory(
            self, box, tmp_path, monkeypatch, empty_path_entry):
        """D1: ``start`` runs where the user stands, often the box's own writable
        workspace.  A module planted there never runs on the host in the hub, neither
        from the launch directory nor through an empty ``PYTHONPATH`` entry.
        (Mutation: drop ``-P`` and ``cwd`` -> the planted argparse runs -> RED.)"""
        socket_path, ctx, _state = box
        planted = tmp_path / "workspace"
        planted.mkdir()
        marker = tmp_path / "planted-ran"
        (planted / "argparse.py").write_text(
            f"open({str(marker)!r}, 'a').write('argparse\\n')\n"
            "raise SystemExit(7)\n")
        monkeypatch.chdir(planted)
        if empty_path_entry:
            monkeypatch.setenv("PYTHONPATH",
                               os.pathsep + os.environ.get("PYTHONPATH", ""))
        hub = HubProcess()
        try:
            ready = hub.start(socket_path, ctx, log_path=None, container=CONTAINER,
                              stderr_path=None)
            assert not marker.exists(), "a planted module ran in the hub"
            assert ready
        finally:
            hub.stop()
        assert not marker.exists()

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
