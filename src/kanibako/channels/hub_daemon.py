"""The per-box HOST helper hub process, which outlives ``kanibako start``
(184-S2 (a) [Jei:2026-10-13]).

A persistent box keeps running after ``start`` returns, so its hub cannot live in the
``start`` process.  :class:`HubProcess` runs the hub as a DETACHED child, the way
``commands.start._spawn_creds_watcher`` runs the creds watcher, and the child serves
until the box is gone.  Every start runs its hub this way, so a box has one hub
lifetime in every mode.

The child reads the launch-built :class:`~kanibako.channels.helper_listener.HelperContext`
from its stdin, pickled: the context carries the launch environment, so it is never
written to a file.  The child takes the box's hub lock, binds the socket, and answers
:data:`HUB_READY` on its stdout.  A hub that still holds the lock serves a box that is
gone, because a running box is reattached and never relaunched, so the new child stops it.

The socket's DIRECTORY is bound into the box (``core_defaults.helper_socket_dest``), so a
hub that binds again reaches a box that is already running.  The child re-binds when the
socket path no longer names its socket (:meth:`HelperHub.listening`).
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import fcntl
import os
import pickle
import select
import signal
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import IO, Any

from kanibako.log import get_logger, setup_logging

log = get_logger("hub_daemon")

#: The line the child writes on its stdout once its socket is bound.
HUB_READY = b"ready\n"
#: How long ``start`` waits for :data:`HUB_READY`, and a stop for the lock to free.
READY_TIMEOUT = 15.0
STOP_TIMEOUT = 60.0  # the hub stops each helper container before it exits
#: Consecutive not-live reads that mean the box is gone (one read per poll, 2 s by default).
MISS_LIMIT = 3
#: How long one liveness call to the runtime may take; a call that hangs is a miss.
PROBE_TIMEOUT = 10.0
#: The child's command-line marker, which a stop checks before it signals a lock holder.
_MODULE = "kanibako.channels.hub_daemon"


def hub_lock_path(socket_path: Path) -> Path:
    """The hub's single-instance lock: ``<hub dir>.lock``, beside the bound directory.

    In the host runtime dir, never in the bound directory: the box can write there.
    """
    hub_dir = socket_path.parent
    return hub_dir.with_name(f"{hub_dir.name}.lock")


# --------------------------------------------------------------------------- #
# The PURE decision.                                                            #
# --------------------------------------------------------------------------- #

class HubAction(Enum):
    """What one watch tick must do."""

    SERVE = "serve"      # keep serving
    REBIND = "rebind"    # the socket path no longer reaches this hub: bind again
    EXIT = "exit"        # the box is gone: stop the hub and its helpers, then exit


def decide_hub(*, misses: int, seen_live: bool, waited: float, grace: float,
               listening: bool) -> HubAction:
    """PURE: a tick's action from the box's liveness and the hub's state.

    *misses* counts the consecutive reads that found the box not live (0: live now).
    A failed probe reads as not live, so the box is gone only after :data:`MISS_LIMIT`
    such reads in a row: a probe that fails fewer times running stops nothing.  The hub
    starts BEFORE its box exists, so a box that is not live yet is waited for, up to
    *grace* seconds.
    """
    if misses >= MISS_LIMIT and (seen_live or waited >= grace):
        return HubAction.EXIT
    return HubAction.SERVE if listening else HubAction.REBIND


# --------------------------------------------------------------------------- #
# The lock, and stopping the process that holds it.                             #
# --------------------------------------------------------------------------- #

def _try_lock(fd: IO[str]) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _holder_pid(lock_path: Path) -> int | None:
    """The pid the holding hub wrote into the lock, when it is a hub process."""
    try:
        pid = int(lock_path.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if _MODULE.encode() in cmdline else None


def stop_hub(socket_path: Path, *, timeout: float = STOP_TIMEOUT) -> bool:
    """Stop the hub that holds *socket_path*'s lock; True once no hub holds it.

    The hub stops its helpers and removes its socket before it exits.  Only a holder
    whose command line is this module is signaled.  Never raises.
    """
    lock_path = hub_lock_path(socket_path)
    try:
        fd = open(lock_path)
    except FileNotFoundError:
        return True  # no hub ever ran here
    except OSError as exc:
        log.warning("helper hub lock %s unreadable: %s", lock_path, exc)
        return False
    try:
        if _try_lock(fd):
            return True
        pid = _holder_pid(lock_path)
        if pid is not None:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
            if _try_lock(fd):
                return True
        return False
    finally:
        fd.close()  # closing releases a lock this call took


def _take_lock(socket_path: Path) -> IO[str] | None:
    """Stop any earlier hub, then hold the lock with this pid in it; ``None`` on failure."""
    if not stop_hub(socket_path):
        return None
    lock_path = hub_lock_path(socket_path)
    fd = open(lock_path, "a+")
    if not _try_lock(fd):
        fd.close()
        return None
    fd.seek(0)
    fd.truncate()
    fd.write(f"{os.getpid()}\n")
    fd.flush()
    return fd


# --------------------------------------------------------------------------- #
# The launch side.                                                              #
# --------------------------------------------------------------------------- #

class HubProcess:
    """``start``'s handle on the hub process it spawns.

    The hub outlives ``start`` only when ``start`` hands it to a box that came up
    (:meth:`release`).  Every other way ``start`` ends stops it: the explicit
    :meth:`stop`, or, for a return between the spawn and the launch, the exit hook.
    """

    def __init__(self) -> None:
        self.socket_path: Path | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._settled = False

    def start(self, socket_path: Path, context: Any, *, log_path: Path | None,
              container: str, stderr_path: Path | None) -> bool:
        """Spawn the hub process and wait for it to bind; True when it answered ready.

        Detached like the creds watcher (its own session, no terminal), so it survives
        ``start`` returning.  Its stderr is APPENDED to *stderr_path*, the one place a
        detached process's warnings reach the user; ``None`` discards them.
        """
        self.socket_path = socket_path
        try:
            payload = pickle.dumps(
                {"socket_path": socket_path, "context": context, "log_path": log_path},
            )
        except (pickle.PicklingError, TypeError, AttributeError) as exc:
            log.warning("helper hub context cannot be handed over: %s", exc)
            return False
        sink = None
        if stderr_path is not None:
            try:
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                sink = open(stderr_path, "a")
            except OSError as exc:
                log.warning("helper hub log unavailable (%s); its warnings will be "
                            "discarded", exc)
        # The child imports THIS kanibako, whatever put it on this process's path.  It
        # imports nothing from the launch directory, which is often the box's own
        # writable workspace: ``-P`` leaves that directory off ``sys.path``, and the
        # child runs in ``/``, where an empty ``PYTHONPATH`` entry would point.
        package_root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (package_root, env.get("PYTHONPATH")) if p)
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-P", "-m", _MODULE, "--container", container],
                cwd="/",
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sink if sink is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            log.warning("helper hub process could not start: %s", exc)
            return False
        finally:
            if sink is not None:
                sink.close()  # the child holds its own descriptor
        proc = self.process
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except OSError as exc:
            log.debug("helper hub did not take its context: %s", exc)
        atexit.register(self._at_exit)
        ready = _read_ready(proc.stdout, READY_TIMEOUT)
        proc.stdout.close()
        return ready

    def release(self) -> None:
        """Leave the hub running: its box is up, and the hub exits when the box is gone
        (see :func:`serve` for how long that takes)."""
        self._settled = True

    def stop(self) -> None:
        """Stop the hub: it stops its helpers, removes its socket, and exits."""
        self._settled = True
        if self.socket_path is not None:
            stop_hub(self.socket_path)

    def _at_exit(self) -> None:
        if not self._settled:
            self.stop()


def _read_ready(stream: IO[bytes], timeout: float) -> bool:
    """True when *stream* yields :data:`HUB_READY` within *timeout* seconds."""
    deadline = time.monotonic() + timeout
    got = b""
    fd = stream.fileno()
    while len(got) < len(HUB_READY):
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            return False
        chunk = os.read(fd, len(HUB_READY) - len(got))
        if not chunk:
            return False
        got += chunk
    return got == HUB_READY


# --------------------------------------------------------------------------- #
# The hub process.                                                              #
# --------------------------------------------------------------------------- #

def _box_live(runtime: Any, container: str) -> bool:
    """Running or paused.  A probe that fails, raises, or outlasts :data:`PROBE_TIMEOUT`
    (the runtime call is killed) reads as NOT live; the hub exits only after
    :data:`MISS_LIMIT` such reads in a row (:func:`decide_hub`).
    """
    from kanibako.commands.stop import _is_live

    try:
        return _is_live(runtime, container, timeout=PROBE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - a failed probe is a miss, never a crash
        log.debug("liveness probe failed for %s: %s", container, exc)
        return False


def serve(hub: Any, *, is_live: Any, stop: threading.Event, poll: float,
          grace: float, clock: Any = time.monotonic) -> None:
    """Watch loop: tick every *poll* seconds until the box is gone or *stop* is set.

    A box that was seen live is gone after :data:`MISS_LIMIT` consecutive not-live
    reads, so the hub decides to exit between ``(MISS_LIMIT - 1) * poll`` and
    ``MISS_LIMIT * poll`` seconds after the box goes (4-6 s at the default 2 s poll).
    It then stops each helper, and ``podman stop`` waits its 10 s for a helper whose
    PID 1 ignores SIGTERM, so the hub is gone 11-14 s after its box.  A foreground
    persistent start whose box is still read running at its end leaves the hub this
    way too, about 18 s after the agent exits.  A probe failure that lasts that long
    ends the hub and stops its helpers, like a box that is gone; a hung probe is
    killed after :data:`PROBE_TIMEOUT` and counts as one failure, so a tick takes at
    most ``poll + 2 * PROBE_TIMEOUT`` seconds and the grace is overrun by at most that.
    """
    began = clock()
    seen_live = False
    misses = 0
    while not stop.wait(poll):
        live = is_live()
        seen_live = seen_live or live
        misses = 0 if live else misses + 1
        action = decide_hub(misses=misses, seen_live=seen_live, waited=clock() - began,
                            grace=grace, listening=hub.listening())
        if action is HubAction.EXIT:
            log.info("box gone; helper hub exiting")
            return
        if action is HubAction.REBIND:
            log.warning("helper socket no longer reaches the hub; binding it again")
            try:
                hub.rebind()
            except OSError as exc:
                log.warning("helper socket could not be bound again: %s", exc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"python3 -m {_MODULE}",
        description="Per-box host helper hub; reads its pickled context on stdin.",
    )
    parser.add_argument("--container", required=True, help="the box's container name")
    parser.add_argument("--poll", type=float, default=2.0,
                        help="seconds between liveness checks")
    parser.add_argument("--grace", type=float, default=600.0,
                        help="seconds to wait for the box to come up")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Take the lock, bind the socket, answer ready, serve until the box is gone."""
    from kanibako.channels.helper_listener import HelperHub, MessageLog

    setup_logging(timestamps=True)
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    payload = pickle.load(sys.stdin.buffer)
    socket_path: Path = payload["socket_path"]
    log_path: Path | None = payload["log_path"]

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    lock = _take_lock(socket_path)
    if lock is None:
        log.warning("an earlier helper hub for %s did not stop; not starting", socket_path)
        return 1
    hub = HelperHub()
    try:
        try:
            hub.start(socket_path, payload["context"],
                      log=MessageLog(log_path) if log_path is not None else None)
        except Exception:
            log.exception("helper hub could not bind %s", socket_path)
            return 1
        os.write(1, HUB_READY)
        # Nobody reads stdout after ready: point it at /dev/null, never a closed pipe.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
        runtime = payload["context"].runtime
        serve(hub, is_live=lambda: _box_live(runtime, args.container), stop=stop,
              poll=args.poll, grace=args.grace)
    finally:
        hub.stop()
        with contextlib.suppress(OSError):
            lock.truncate(0)  # no stale pid for a later stop to read
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
