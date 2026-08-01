"""Per-box HOST-side credential-writeback watcher (increment D — the trusted half).

The always-on-instance design (``split-brain-persistence-DESIGN.md`` GAP-1) needs a
box's in-box credential refresh (a panel/agent login that refreshes a SHARED token)
to reach the host credential STORE PROMPTLY — provider refresh-token rotation means a
panel refresh in one box can lock another box out until the new token lands in the
store, so writeback is a CORRECTNESS need, not just hygiene.

The LOAD-BEARING TRUST INVARIANT (Jei, 2026-07-11): the host credential store must
NEVER be writable from inside a box — a bind mount is not process-scoped, so the
untrusted in-box agent would inherit any store-write handle.  So the WRITE stays
HOST-side (trusted).  The in-box supervisor only SIGNALS a detach by edge-triggering a
flag in its OWN box-home (:meth:`kanibako.box_supervisor.BoxSupervisor._on_detach`);
THIS module is the trusted HOST daemon that watches that flag (box-home is host-visible
via the existing bind mount) and does the privileged box-home → store copy through the
EXISTING host writeback (:func:`kanibako.commands.start.writeback_session_credentials`,
unchanged logic).

Design for testability mirrors :mod:`kanibako.box_lifecycle` / :mod:`kanibako.box_supervisor`:
the PURE decision (:func:`decide_watch`) is split from the impure box probe / flag I/O /
writeback, and EVERY side effect funnels through an injected callable (plus an injected
``sleep``), so the whole loop is driven in unit tests with no real box, FS, or waiting.
Every probe is TOLERANT — a watcher that crashed on a transient error would silently
stop propagating credentials — so a failing writeback is logged, never raised.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import sys
import time
from collections.abc import Callable, Iterator
from enum import Enum
from pathlib import Path

from kanibako.log import get_logger
from kanibako.paths import xdg

log = get_logger("creds_watcher")

#: The box-local RELPATH of the credential-writeback SIGNAL flag — the SINGLE source
#: of truth for both ends of the contract.  The in-box supervisor writes
#: ``Path.home()/<relpath>`` (via ``--creds-flag`` = ``GUEST_HOME/<relpath>``); the host
#: reads/clears ``proj.shell_path/<relpath>`` (the SAME file via the box-home bind
#: mount, since ``proj.shell_path`` on the host IS the box's ``$HOME`` mount source).
CREDS_DIRTY_RELPATH = ".kanibako/creds-dirty"

# Injected-dependency signatures (mirrors box_supervisor's ``_Runner`` / ``_Sleeper``):
# a box-liveness probe, a "is the flag set?" reader, the privileged writeback (which is
# the SOLE flag-clearer — it self-clears on success), and the loop sleep.  Tests inject
# fakes so nothing touches a real box / FS / store.
_IsRunning = Callable[[], bool]
_FlagReader = Callable[[], bool]
_Writeback = Callable[[], None]
_Sleeper = Callable[[float], None]


# --------------------------------------------------------------------------- #
# Flag helpers (host-side; TOLERANT) — shared by the watcher AND the lazy         #
# fallback / hygiene on the existing host writeback sites (Part 3).               #
# --------------------------------------------------------------------------- #

def creds_dirty_flag_path(project_home: Path) -> Path:
    """The host path of a box's creds-dirty flag (``project_home`` = ``proj.shell_path``)."""
    return Path(project_home) / CREDS_DIRTY_RELPATH


def read_creds_dirty(project_home: Path) -> bool:
    """True iff the box's creds-dirty flag is present.  Tolerant — any error ⇒ False."""
    try:
        return creds_dirty_flag_path(project_home).exists()
    except OSError as exc:
        log.debug("read_creds_dirty: probe failed for %s: %s", project_home, exc)
        return False


def clear_creds_dirty(project_home: Path) -> None:
    """Remove the box's creds-dirty flag (edge-trigger reset).  Tolerant — never raises.

    Idempotent: an already-absent flag (``missing_ok``) is fine, and any other
    ``OSError`` is swallowed (a stale flag merely gets re-processed next time — a
    harmless duplicate writeback, never a crash).
    """
    try:
        creds_dirty_flag_path(project_home).unlink(missing_ok=True)
    except OSError as exc:
        log.debug("clear_creds_dirty: unlink failed for %s: %s", project_home, exc)


# --------------------------------------------------------------------------- #
# The shared STORE-write lock — serialize writeback across host ops + watcher.  #
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def creds_store_lock() -> "Iterator[None]":
    """Serialize credential-STORE writes across concurrent host ops + this watcher.

    The writeback copies box-home creds into the SHARED store (host home / workset
    dir).  Two writers to the same store must not interleave: a ``kanibako stop``
    writeback, a foreground reattach writeback, and this per-box daemon can all fire
    at once.  There is no pre-existing lock on the writeback itself (the launch-path
    flock is the EPHEMERAL session lock, SKIPPED for persistent boxes), so this is a
    narrow host-wide mutex around the store write that EVERY writeback path shares —
    the watcher and :func:`kanibako.commands.start.writeback_session_credentials`
    both enter it, so concurrent writes serialize.

    A single host-wide lock (over-serializing across worksets) is deliberate: cred
    writebacks are rare + fast, so a global mutex is simpler and race-free.  TOLERANT:
    a lock file that cannot be created / locked degrades to proceeding UNLOCKED (a
    writeback must never be BLOCKED by a lock-infra hiccup) rather than raising.
    """
    lock_path = xdg("XDG_STATE_HOME", ".local/state") / "kanibako" / "creds-writeback.lock"
    fd = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception as exc:
        # Broad by design: this lock must NEVER block/abort a writeback, so ANY
        # acquisition failure (a missing dir, a non-lockable fd) degrades to unlocked.
        log.debug("creds store lock unavailable (%s); proceeding unlocked", exc)
        if fd is not None:
            try:
                fd.close()
            except Exception:
                pass
            fd = None
    try:
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# The PURE decision (the heart of the unit tests).                              #
# --------------------------------------------------------------------------- #

class WatchAction(Enum):
    """What a single watch tick must DO, from (box-liveness, flag-dirty)."""

    NONE = "none"                        # box up, flag clean → keep watching
    WRITEBACK = "writeback"              # box up, flag dirty → writeback (self-clears on success)
    FINAL_WRITEBACK = "final_writeback"  # box gone, flag dirty → one last writeback, then EXIT
    EXIT = "exit"                        # box gone, flag clean → nothing to do, EXIT


def decide_watch(box_running: bool, dirty: bool) -> WatchAction:
    """PURE: decide a tick's action from box liveness + the dirty flag.

    * box RUNNING → :data:`WatchAction.WRITEBACK` when the flag is dirty (a detach
      signalled a possible refresh), else :data:`WatchAction.NONE` (keep watching).
    * box GONE → the box is being torn down / stopped, so the watcher SELF-TERMINATES:
      :data:`WatchAction.FINAL_WRITEBACK` when the flag is still dirty (catch a
      last-moment refresh before exiting), else :data:`WatchAction.EXIT`.

    Deterministic and side-effect free, so the loop's whole logic is exhaustively
    unit-testable without a real box.
    """
    if box_running:
        return WatchAction.WRITEBACK if dirty else WatchAction.NONE
    return WatchAction.FINAL_WRITEBACK if dirty else WatchAction.EXIT


# --------------------------------------------------------------------------- #
# The watcher.                                                                  #
# --------------------------------------------------------------------------- #

class CredsWatcher:
    """Polls a box's creds-dirty flag and does the trusted writeback on the signal.

    Impure by nature (it probes the box, reads/clears a flag, copies credentials, and
    sleeps), but every side effect is funnelled through an injected callable so tests
    drive it deterministically and instantly.  The pure :func:`decide_watch` holds the
    per-tick logic; this class only sequences the injected effects around it.
    """

    def __init__(
        self,
        *,
        is_running: _IsRunning,
        read_dirty: _FlagReader,
        writeback: _Writeback,
        sleep: _Sleeper = time.sleep,
        poll_interval: float = 2.0,
    ) -> None:
        self._is_running = is_running
        self._read_dirty = read_dirty
        self._writeback = writeback
        self._sleep = sleep
        self._poll = poll_interval

    def _safe_writeback(self) -> None:
        """Run the injected writeback, swallowing ANY exception (log-not-crash).

        A store-writeback failure (a transient FS error, a concurrent host op) must
        NEVER stop the watcher — it would silently stop propagating every later
        credential refresh.  Logged and dropped, and — critically — the watcher does
        NOT clear the flag itself: the writeback is the SOLE clearer and clears ONLY on
        success (:func:`kanibako.commands.start.writeback_session_credentials`, D Part
        3).  So a FAILED writeback leaves the flag SET, and the next tick RETRIES it —
        dropping the signal here would defeat D's whole rotation-lockout guarantee.
        """
        try:
            self._writeback()
        except Exception:
            log.exception("creds writeback failed; flag left set for retry")

    def run(self) -> int:
        """Watch loop: poll → :func:`decide_watch` → act; self-terminate once the box is gone.

        Each tick reads the flag + probes box liveness (both tolerant), then acts on
        the pure decision.  The flag is cleared ONLY by the writeback itself, and ONLY
        on success (Part 3) — the watcher NEVER clears it directly:

        * dirty + box UP → run the writeback (it self-clears on success); a FAILED
          writeback leaves the flag set → RETRIED next tick (strictly better than
          dropping the pending refresh signal).
        * box GONE + dirty → one FINAL writeback (self-clears on success); on failure
          the flag remains for the lazy fallback (the next host ``kanibako`` op) —
          acceptable, the box is gone.  Then return.
        * box GONE + clean → return.

        Returns 0.
        """
        while True:
            dirty = self._read_dirty()
            running = self._is_running()
            action = decide_watch(running, dirty)
            if action is WatchAction.WRITEBACK:
                self._safe_writeback()
            elif action is WatchAction.FINAL_WRITEBACK:
                log.info("box gone with a pending creds signal; final writeback then exit")
                self._safe_writeback()
                return 0
            elif action is WatchAction.EXIT:
                log.info("box gone; nothing pending — creds watcher exiting")
                return 0
            self._sleep(self._poll)


# --------------------------------------------------------------------------- #
# CLI entry point — re-resolves host config like ``kanibako stop`` and runs.     #
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m kanibako.launch.creds_watcher",
        description="Per-box host-side credential-writeback watcher (increment D).",
    )
    parser.add_argument(
        "--box", default=None,
        help="box subject (name or path) to watch; default resolves from cwd",
    )
    parser.add_argument(
        "--poll", type=float, default=2.0, help="seconds between watch-loop ticks",
    )
    return parser


def _single_instance_lock(lock_path: Path) -> "object | None":
    """Take a single-instance host lock for this box, or ``None`` when another holds it.

    A relaunch of a box must not stack watchers, so each watcher holds an exclusive,
    non-blocking flock keyed on the box identity (``proj.metadata_path`` — per box).
    Returns the OPEN file object (the caller keeps it for the process lifetime — the
    lock releases when it is closed / the process exits) on success, or ``None`` when
    the lock is already held (a live prior watcher) so this launch exits quietly.
    Tolerant: a lock path that cannot even be created returns a fresh (unlocked) handle
    so the watcher still runs rather than crashing on a filesystem quirk.
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
    except OSError as exc:
        log.debug("single-instance lock unavailable (%s); running unlocked", exc)
        return object()  # a non-None sentinel: run, but hold no real lock
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def _resolve_watch_context(box: str | None):
    """Re-resolve the host launch context for *box* (mirrors ``kanibako stop``).

    Returns ``(runtime, proj, container_name, target, auth_src)`` or ``None`` when the
    box cannot be resolved / has no shared-credential agent (nothing to watch).
    """
    from kanibako.agent_config import agent_settings_path
    from kanibako.agent_ref import harness_of
    from kanibako.commands.start import _resolve_box_auth_source
    from kanibako.config import config_file_path, load_config
    from kanibako.runtime.container import ContainerRuntime
    from kanibako.paths import load_std_paths, resolve_box_target, xdg
    from kanibako.targets import resolve_target
    from kanibako.utils import container_name_for

    runtime = ContainerRuntime()
    config = load_config(config_file_path(xdg("XDG_CONFIG_HOME", ".config")))
    std = load_std_paths(config)
    proj = resolve_box_target(std, config, box, initialize=False)
    container_name = container_name_for(proj)

    # The box's agent comes from its KANIBAKO_AGENT launch stamp (same as stop.py) —
    # it names which plugin's cred lifecycle to run + drives the auth-tier resolution.
    agent = runtime.inspect_env(container_name, "KANIBAKO_AGENT")
    if not agent:
        log.info("box %s has no agent stamp; nothing to write back", container_name)
        return None
    target = resolve_target(harness_of(agent), proj.project_path)
    # ⚑ The §1A SELECTION LEVEL is REQUIRED (P7): ``meta.box.auth.workset_path``
    # resolves ``@workset.auth.path/@system.agent``, so without it the per-agent
    # credential SOURCE collapses to the workset auth ROOT — the watcher would sync
    # a different directory than the launch delivered from, and in a multi-agent
    # workset two agents would share one dir. The ``KANIBAKO_AGENT`` stamp IS the
    # resolved selection for a running box.
    auth_src = _resolve_box_auth_source(
        std=std,
        proj=proj,
        agent_name=agent,
        system_settings_path=std.settings,
        agent_cfg_path=agent_settings_path(std.agents, agent),
        selection_level={"system.agent": agent},
    )
    return runtime, proj, container_name, target, auth_src


def main(argv: list[str] | None = None) -> int:
    """CLI: resolve the box, take the single-instance lock, run the watch loop.

    ``python3 -m kanibako.launch.creds_watcher --box <subject> [--poll SEC]``

    A PRIVATE box (``auth_src.creds_shared`` False) never propagates credentials, so the
    watcher no-ops and exits immediately (the host spawner also skips spawning one).
    Best-effort throughout: a resolution failure logs and exits 0 (a watcher that
    can't resolve its box is simply not needed — never a crash that surfaces to a
    user, since it runs detached).
    """
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    try:
        ctx = _resolve_watch_context(args.box)
    except Exception:
        log.exception("creds watcher could not resolve its box; exiting")
        return 0
    if ctx is None:
        return 0
    runtime, proj, container_name, target, auth_src = ctx

    if not auth_src.creds_shared:
        # Private/box-tier box: writeback is a no-op, so watching is pointless.
        log.info("box %s is private (no shared creds); watcher exiting", container_name)
        return 0

    lock = _single_instance_lock(proj.metadata_path / ".kanibako-creds-watcher.lock")
    if lock is None:
        log.info("another creds watcher already holds the lock for %s; exiting", container_name)
        return 0

    from kanibako.commands.start import writeback_session_credentials

    watcher = CredsWatcher(
        is_running=lambda: runtime.is_running(container_name),
        read_dirty=lambda: read_creds_dirty(proj.shell_path),
        # The writeback IS the sole flag-clearer (self-clears on success), so the
        # watcher takes no separate clear callable — a failed write keeps the flag set.
        writeback=lambda: writeback_session_credentials(target, proj, auth_src=auth_src),
        sleep=time.sleep,
        poll_interval=args.poll,
    )
    log.info("creds watcher started for box %s", container_name)
    try:
        return watcher.run()
    finally:
        # Release the single-instance lock (also released on process exit).
        try:
            if hasattr(lock, "close"):
                lock.close()  # type: ignore[union-attr]
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
