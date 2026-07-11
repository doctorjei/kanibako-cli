"""Box always-on-agent SUPERVISOR — the PID-1 keep-alive that outlives sessions.

The always-on-instance design (`split-brain-persistence-DESIGN.md`, "E2 BUILD
DESIGN") makes a box's keep-alive PID-1 a SUPERVISOR: it runs the agent in a
DETACHED tmux session, watches client attach/detach via :mod:`kanibako.box_lifecycle`,
and SELF-HEALS the agent (restart with `--continue` + a continue-marker) when it
dies — so the box persists independent of any one agent session (design principle
B: only genuine exit-of-everything, or an explicit ``kanibako stop``, tears the box
down).

This module is INCREMENT 2 (E2a): the supervisor MODULE ONLY.  It makes NO
launch-model changes — it does not touch ``start.py`` / ``_run_container`` (that is
E2b, which will `exec python3 -m kanibako.box_supervisor ...` as PID-1).  It also
does NOT implement eviction / handoff / single-state enforcement (design increment
4, deferred): there is no agent eviction or process-group killing here.

Design for testability — the PURE decision logic (:func:`decide`) is split from the
impure tmux actions, and EVERY subprocess call funnels through an injectable runner
(and an injectable ``sleep``), so tests drive the whole thing with no real tmux, no
real agent, and no real waiting.  Like :mod:`kanibako.box_lifecycle`, every
tmux/subprocess call is TOLERANT: a missing tmux binary or a non-zero exit resolves
to a safe falsy value and is logged, never raised — the supervisor IS PID-1, so a
probe or action that crashed the loop would take the whole box down with it.
"""

from __future__ import annotations

import argparse
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from types import FrameType

from kanibako.box_lifecycle import (
    AttachState,
    LifecycleEvent,
    classify_transition,
    snapshot_attach_state,
)
from kanibako.log import get_logger

log = get_logger("box_supervisor")

#: The continue-marker string a self-heal restart delivers (via ``tmux send-keys``)
#: as a real acting turn so a resurrected agent autonomously resumes the prior
#: task(s) (design ``split-brain-persistence-DESIGN.md`` §108).  Defined ONCE here so
#: the host-side launch wiring (``commands/start.py`` → ``--marker``) and the
#: in-box supervisor share a single source of truth — never a duplicated literal.
CONTINUE_MARKER = "[Agent handoff - Continue prior task(s)]"

# The subprocess-runner signature the tmux actions call.  ``subprocess.run``
# matches it; tests inject a fake so nothing touches a real tmux server.  Shared
# in spirit with box_lifecycle's ``_Runner``.
_Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# The ``time.sleep`` signature the loop / backoff use; injectable so tests never
# actually wait.
_Sleeper = Callable[[float], None]


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorConfig:
    """Immutable configuration for a :class:`BoxSupervisor`.

    * *session* — the tmux session name the agent lives in (E2b keeps ``"kanibako"``
      for attach/reattach compat).
    * *start_argv* — the agent launch grammar for the INITIAL start (entrypoint +
      args), run as ``tmux new-session -d -s <session> -- <start_argv...>``.
    * *continue_argv* — the launch grammar for a self-heal RESTART (the
      ``--continue`` form, which re-reads the box's ``~/.claude`` history).  Defaults
      to *start_argv* when a caller does not distinguish the two.
    * *marker* — the continue-marker string sent (via ``tmux send-keys``) as a real
      acting turn to a restarted agent so it autonomously resumes (design:
      ``[Agent handoff - Continue prior task(s)]``).
    * *poll_interval* — seconds between watch-loop ticks.
    * *max_restart_retries* — bounded self-heal attempts before giving up (principle
      B: on exhaustion PID-1 exits so the box can stop).
    * *backoff_base* — base seconds for the exponential self-heal backoff.
    * *send_keys_retries* / *send_keys_delay* — bounded retry so ``send-keys`` lands
      after the freshly created pane is ready.
    * *on_agent_exit* — the LAUNCH-INTENT-AWARE policy for what happens when the
      agent exits (design ``split-brain-persistence-DESIGN.md`` §85-96, E2c):
      ``"self-heal"`` (the default, detached / future-panel launches) keeps the
      always-on bounded-retry restart; ``"teardown"`` (a FOREGROUND CLI launch, where
      a human is the driver) treats an agent EXIT as a NORMAL termination and lets
      PID-1 return so the box closes — no self-heal loop while a CLI is the surface.
      Any value other than ``"teardown"`` is treated as ``"self-heal"`` (safe default).
    """

    session: str
    start_argv: list[str]
    continue_argv: list[str]
    marker: str
    poll_interval: float = 2.0
    max_restart_retries: int = 3
    backoff_base: float = 0.5
    send_keys_retries: int = 3
    send_keys_delay: float = 0.1
    on_agent_exit: str = "self-heal"


# ---------------------------------------------------------------------------
# Action model + the PURE decision function (the heart of the unit tests).
# ---------------------------------------------------------------------------

class ActionKind(Enum):
    """What the supervisor must DO for a tick (besides the detach hook)."""

    NONE = "none"
    SELF_HEAL = "self_heal"


@dataclass(frozen=True)
class SupervisorAction:
    """The decision a single watch-loop tick produces.

    * *kind* — :data:`ActionKind.SELF_HEAL` when the agent session has died (restart
      it with the continue grammar + marker); otherwise :data:`ActionKind.NONE`.
    * *fire_detach_hook* — ``True`` when this tick is a DETACH transition (a client
      surface that was attached is gone), so the loop calls the best-effort
      :meth:`BoxSupervisor._on_detach` hook (GAP-1 cred-writeback fills it in D).
    """

    kind: ActionKind = ActionKind.NONE
    fire_detach_hook: bool = False


def decide(
    prev_state: AttachState,
    cur_state: AttachState,
    agent_alive: bool,
) -> SupervisorAction:
    """PURE: decide a tick's action from the attach-state transition + agent liveness.

    Two ORTHOGONAL signals combine:

    * agent liveness — a DEAD agent session (``agent_alive`` False) ⇒
      :data:`ActionKind.SELF_HEAL` regardless of any client transition (the always-on
      guarantee: whenever no instance is live, restart one).
    * the client-attach transition — :func:`classify_transition` over
      ``prev_state`` → ``cur_state``; a :data:`LifecycleEvent.DETACH` sets
      *fire_detach_hook* (ATTACH / NONE do not).  This is independent of liveness, so
      an "agent died AND a surface detached in the same tick" yields BOTH (self-heal
      and the detach hook).

    Deterministic and side-effect free over its inputs — the whole loop's logic lives
    here so tests can exhaust it without any tmux.
    """
    fire_detach_hook = classify_transition(prev_state, cur_state) is LifecycleEvent.DETACH
    kind = ActionKind.NONE if agent_alive else ActionKind.SELF_HEAL
    return SupervisorAction(kind=kind, fire_detach_hook=fire_detach_hook)


# ---------------------------------------------------------------------------
# The supervisor.
# ---------------------------------------------------------------------------

class BoxSupervisor:
    """PID-1 keep-alive that supervises an agent in a detached tmux session.

    Impure by nature (it shells ``tmux`` and sleeps), but every side effect is
    funnelled through the injected *run* / *sleep* so tests drive it deterministically
    and instantly.  Detection is DELEGATED to :mod:`kanibako.box_lifecycle`
    (:func:`snapshot_attach_state` + :func:`classify_transition` via :func:`decide`) —
    this class never re-implements attach detection.

    Lifecycle: :meth:`run_forever` starts the agent if absent, then loops —
    snapshot → decide → act (fire the detach hook, self-heal a dead agent) — until an
    explicit teardown (SIGTERM / :meth:`teardown`) or a self-heal that exhausts its
    bounded retries (principle B: then PID-1 returns so the box can stop).
    """

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        run: _Runner = subprocess.run,
        sleep: _Sleeper = time.sleep,
        proc_cmdlines: Iterable[str] | None = None,
    ) -> None:
        self.config = config
        self._run = run
        self._sleep = sleep
        # When provided, a fixed process-cmdline listing handed to
        # snapshot_attach_state (tests inject it to skip the real ``/proc`` walk);
        # ``None`` ⇒ each snapshot collects fresh from ``/proc`` (the real PID-1 path).
        self._proc_cmdlines = None if proc_cmdlines is None else list(proc_cmdlines)
        self._stop = False

    # -- tmux action helpers (impure; tolerant; injectable ``run``) ----------

    def _run_tmux(self, args: list[str]) -> int | None:
        """Run ``tmux <args>`` via the injected runner; return its rc, or ``None``.

        Centralises tolerance: a missing tmux binary (``FileNotFoundError``) or any
        other ``OSError`` resolves to ``None`` (logged at debug), never an exception,
        so a tmux hiccup can never crash the loop.
        """
        try:
            proc = self._run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            log.debug("tmux %s failed to run: %s", args[0] if args else "", exc)
            return None
        return proc.returncode

    def _tmux_output(self, args: list[str]) -> str | None:
        """Run ``tmux <args>`` via the injected runner; return its STDOUT, or ``None``.

        The stdout sibling of :meth:`_run_tmux`, for the probes that read a tmux
        FORMAT string (``display-message``) rather than only its rc.  Same tolerance:
        a missing tmux binary / ``OSError`` OR a non-zero rc all resolve to ``None``
        (never an exception), so a tmux hiccup can never crash the loop.
        """
        try:
            proc = self._run(
                ["tmux", *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            log.debug("tmux %s failed to run: %s", args[0] if args else "", exc)
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _set_remain_on_exit(self) -> None:
        """Enable ``remain-on-exit`` for the agent session so a DEAD pane PERSISTS.

        Without this, a pane whose agent process exits is destroyed and tmux exposes
        no exit status; WITH it on, the pane becomes a DEAD pane carrying the real
        exit code in ``#{pane_dead_status}`` — which SURVIVES the ``exec`` in the
        secret-export shim (a naive ``agent; echo $?`` wrapper would not fire).
        :meth:`agent_pane_dead_status` reads that code.  Issued as a SEPARATE
        ``set-option`` right after ``new-session`` so the start/restart argv stays
        byte-stable (the exact-argv unit tests keep matching).  Best-effort and
        tolerant: a tmux that rejects the option merely degrades liveness to
        has-session-only (the pre-E2d behavior) — never a crash.
        """
        rc = self._run_tmux(
            ["set-option", "-t", self.config.session, "remain-on-exit", "on"]
        )
        if rc not in (0, None):
            log.debug("_set_remain_on_exit: tmux set-option rc=%s", rc)

    def start_agent_session(self) -> bool:
        """Start the agent DETACHED: ``tmux new-session -d -s <session> -- <start_argv>``.

        The ``-d`` starts the session detached (agent executing, no client attached);
        ``--`` terminates tmux option parsing so the agent grammar is taken verbatim.
        On success, arms ``remain-on-exit`` (:meth:`_set_remain_on_exit`) so the
        agent's real exit code survives its death as a dead-pane status.  Returns
        ``True`` on rc 0.
        """
        rc = self._run_tmux(
            ["new-session", "-d", "-s", self.config.session, "--", *self.config.start_argv]
        )
        if rc != 0:
            log.warning("start_agent_session: tmux new-session rc=%s", rc)
            return False
        self._set_remain_on_exit()
        return True

    def restart_agent_session(self) -> bool:
        """Restart the agent for self-heal, with the CONTINUE grammar + marker.

        Starts ``tmux new-session -d -s <session> -- <continue_argv>`` (the
        ``--continue`` form re-reads the box's ``~/.claude`` history), then delivers
        the continue-marker via :meth:`_send_marker` so the successor gets it as a
        REAL acting turn (autonomous resume, no human needed).  Returns ``True`` when
        the new-session started (rc 0); marker delivery is best-effort and logged.

        With ``remain-on-exit`` a dead agent leaves its session PRESENT (a dead pane)
        still holding the canonical name, so a fresh ``new-session`` with the same
        name would COLLIDE.  Kill the (dead) session FIRST — tolerant no-op when
        nothing is there — so the restart can reuse the ``kanibako`` name, then arm
        ``remain-on-exit`` again for the successor.
        """
        self.kill_agent_session()
        rc = self._run_tmux(
            ["new-session", "-d", "-s", self.config.session, "--", *self.config.continue_argv]
        )
        if rc != 0:
            log.warning("restart_agent_session: tmux new-session rc=%s", rc)
            return False
        self._set_remain_on_exit()
        if not self._send_marker():
            log.warning("restart_agent_session: continue-marker send-keys did not land")
        return True

    def _send_marker(self) -> bool:
        """Send the continue-marker to the session via ``tmux send-keys`` (bounded retry).

        A freshly created pane may not be ready the instant ``new-session`` returns, so
        retry up to ``send_keys_retries`` times with a small ``send_keys_delay`` between
        attempts.  Emits ``send-keys -t <session> '<marker>' Enter`` — the trailing
        ``Enter`` submits it as a real user turn.  Returns ``True`` once a send lands.
        """
        for attempt in range(1, self.config.send_keys_retries + 1):
            rc = self._run_tmux(
                ["send-keys", "-t", self.config.session, self.config.marker, "Enter"]
            )
            if rc == 0:
                return True
            if attempt < self.config.send_keys_retries:
                self._sleep(self.config.send_keys_delay)
        return False

    def agent_pane_dead_status(self) -> int | None:
        """Return the agent pane's DEAD exit status, or ``None`` when it is not dead.

        With ``remain-on-exit on`` (armed at start/restart) a pane whose agent process
        exits stays in the session as a DEAD pane, and tmux exposes its real exit code
        via ``#{pane_dead_status}``.  Read it with ``tmux display-message -p -t
        <session> '#{pane_dead_status}'``: tmux prints the integer exit code for a
        DEAD pane and an EMPTY string for a live one.

        Tolerant like every probe here (PID-1 must never die on a tmux hiccup): a
        missing tmux / dead server / no session (non-zero rc → ``None`` output) OR
        empty / unparseable output all resolve to ``None`` — treated by callers as
        "not dead / unknown" — never an exception.  Returns the parsed ``int`` only
        for a genuinely dead pane.
        """
        out = self._tmux_output(
            ["display-message", "-p", "-t", self.config.session, "#{pane_dead_status}"]
        )
        if out is None:
            return None
        out = out.strip()
        if not out:
            return None
        try:
            return int(out)
        except ValueError:
            log.debug("agent_pane_dead_status: unparseable pane_dead_status %r", out)
            return None

    def agent_session_alive(self) -> bool:
        """True iff the agent session EXISTS and its pane is NOT dead.

        ``tmux has-session`` alone is no longer sufficient: with ``remain-on-exit on``
        the session PERSISTS after the agent process exits (a dead pane), so
        ``has-session`` stays rc 0.  Liveness is therefore has-session (rc 0) AND
        :meth:`agent_pane_dead_status` is ``None`` (no dead pane).  Tolerant
        throughout — a missing tmux / dead server / no such session all resolve to
        ``False`` (not alive), never an exception.
        """
        if self._run_tmux(["has-session", "-t", self.config.session]) != 0:
            return False
        return self.agent_pane_dead_status() is None

    def kill_agent_session(self) -> None:
        """Kill the agent session: ``tmux kill-session -t <session>`` (teardown only).

        Used on explicit teardown (SIGTERM / :meth:`teardown`).  Tolerant of an
        already-absent session / missing tmux — logs, never raises.  This is NOT
        eviction / process-group handling (increment 4): it is the total-teardown
        kill of the supervised session on box shutdown.
        """
        rc = self._run_tmux(["kill-session", "-t", self.config.session])
        if rc not in (0, None):
            log.debug("kill_agent_session: tmux kill-session rc=%s", rc)

    # -- snapshot ------------------------------------------------------------

    def _snapshot(self) -> AttachState:
        """Probe the current client-attach state (delegates to box_lifecycle)."""
        return snapshot_attach_state(
            self.config.session,
            run=self._run,
            proc_cmdlines=self._proc_cmdlines,
        )

    # -- self-heal -----------------------------------------------------------

    def _self_heal(self) -> bool:
        """Restart a dead agent with bounded retry + exponential backoff.

        Up to ``max_restart_retries`` attempts: each :meth:`restart_agent_session`,
        then check :meth:`agent_session_alive`; a live session ⇒ success (stop
        retrying).  Between failed attempts, ``sleep(backoff_base * 2**(n-1))``.  On
        exhaustion returns ``False`` so :meth:`run_forever` exits (principle B: no
        agent + no one watching → let the box stop, don't spin).
        """
        for attempt in range(1, self.config.max_restart_retries + 1):
            log.info("self-heal: restart attempt %d/%d", attempt, self.config.max_restart_retries)
            self.restart_agent_session()
            if self.agent_session_alive():
                log.info("self-heal: agent session live after attempt %d", attempt)
                return True
            if attempt < self.config.max_restart_retries:
                self._sleep(self.config.backoff_base * (2 ** (attempt - 1)))
        log.error(
            "self-heal: exhausted %d restart attempts; giving up",
            self.config.max_restart_retries,
        )
        return False

    # -- detach hook (best-effort POINT filled by increment D) ---------------

    def _on_detach(self) -> None:
        """Best-effort HOOK POINT fired on a DETACH tick — a NO-OP here.

        Increment D (GAP-1 credential writeback) fills this in to write a
        panel-refreshed token back to the host store on client detach.  E2a ships only
        the point so D wires in WITHOUT touching the loop.  Contract: best-effort and
        idempotent — the loop calls it through :meth:`_safe_on_detach`, so a raising
        implementation is swallowed and never breaks the supervisor.
        """
        log.debug("on-detach hook (no-op in E2a)")

    def _safe_on_detach(self) -> None:
        """Call :meth:`_on_detach`, swallowing ANY exception (the loop must not die)."""
        try:
            self._on_detach()
        except Exception:
            log.exception("on-detach hook raised; ignored (supervisor loop continues)")

    # -- teardown / signals --------------------------------------------------

    def teardown(self) -> None:
        """Total teardown: signal the loop to exit and kill the agent session.

        Design principle B (teardown = TOTAL): an explicit ``kanibako stop`` (podman
        stop → SIGTERM to PID-1) kills everything.  Sets the loop-exit flag then kills
        the supervised session; :meth:`run_forever` returns on its next check.
        """
        log.info("teardown requested; killing agent session and exiting loop")
        self._stop = True
        self.kill_agent_session()

    def _handle_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """SIGTERM handler → :meth:`teardown` (factored out so tests call it directly)."""
        log.info("received signal %s", signum)
        self.teardown()

    def install_signal_handlers(self) -> None:
        """Install the SIGTERM handler (best-effort; a no-op off the main thread).

        Registering a signal handler outside the main thread raises ``ValueError``;
        that (and any ``OSError``) is tolerated so the supervisor still runs — teardown
        then only comes via container kill, which is acceptable.
        """
        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except (ValueError, OSError) as exc:
            log.debug("could not install SIGTERM handler: %s", exc)

    # -- the watch loop ------------------------------------------------------

    def run_forever(self) -> int:
        """Run the supervise loop until teardown, agent-exit, or self-heal exhaustion.

        Installs the SIGTERM handler, starts the agent if absent (the warm-box 1b case
        leaves a live agent alone), then loops: snapshot → :func:`decide` → act (fire
        the detach hook, then respond to a dead agent per the launch-intent policy).
        Each tick's body is guarded so a raising probe/action is logged and the loop
        CONTINUES (PID-1 must not die on a transient error).

        The response to a DEAD agent is LAUNCH-INTENT AWARE (``config.on_agent_exit``,
        E2c) — the pure :func:`decide` still just reports the SELF_HEAL signal (a
        transition/liveness fact); this loop decides what to DO with it, keeping decide
        byte-identical and the policy in exactly one place:

        * ``"self-heal"`` (default; detached / future panel) → today's behavior:
          bounded-retry restart, and the one clean exit is a self-heal that EXHAUSTS
          its retries (returns 0 so the box can stop).
        * ``"teardown"`` (foreground CLI, human present) → an agent exit is a NORMAL
          termination: return the AGENT's own exit code (from the dead pane's
          ``#{pane_dead_status}``; 0 for a clean exit, 1 when dead-but-unknown) so
          PID-1 exits with a TRUTHFUL code and the box closes — a supervised agent
          CRASH surfaces via the host path instead of masquerading as success (E2d).
          No self-heal loop while a CLI is the driver.  A failed INITIAL start
          likewise returns non-zero so the start error surfaces via the host path.

        Returns a process exit code.
        """
        self.install_signal_handlers()
        teardown_on_exit = self.config.on_agent_exit == "teardown"
        # Startup runs BEFORE the per-tick guard, so guard it too: a probe raising
        # here (e.g. an unexpected snapshot failure) must not kill PID-1 before the
        # loop even begins.  On any startup hiccup, degrade to "no attach" and enter
        # the loop — it self-heals (or, under teardown, closes on) a missing agent on
        # its first tick.
        try:
            if not self.agent_session_alive():
                log.info("no live agent session at startup; starting one detached")
                if not self.start_agent_session() and teardown_on_exit:
                    # Foreground CLI launch-intent: a failed INITIAL start is a start
                    # error to surface via the host (the human is present) — do NOT
                    # self-heal, let PID-1 exit non-zero so the box closes and the host
                    # reports it (design §86-88, cold-start-error-human-direct).
                    log.error("initial agent start failed under teardown policy; exiting")
                    return 1
            prev = self._snapshot()
        except Exception:
            log.exception("supervisor startup probe raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                cur = self._snapshot()
                alive = self.agent_session_alive()
                action = decide(prev, cur, alive)
                if action.fire_detach_hook:
                    self._safe_on_detach()
                if action.kind is ActionKind.SELF_HEAL:
                    if teardown_on_exit:
                        # Propagate the agent's TRUE exit code as PID-1's own, so the
                        # container's exit code (and thus the host's foreground error
                        # handling) is TRUTHFUL — a supervised agent CRASH surfaces
                        # instead of masquerading as a clean exit (E2d).  Read the
                        # dead pane's ``#{pane_dead_status}``; a clean status-0 exit
                        # ⇒ 0, and a dead-but-unknown agent (status unreadable, or the
                        # session vanished entirely) ⇒ 1 — a failure, not a success.
                        status = self.agent_pane_dead_status()
                        code = 1 if status is None else status
                        log.info(
                            "agent exited under teardown policy (dead_status=%s); "
                            "closing box with rc %d",
                            status,
                            code,
                        )
                        return code
                    if not self._self_heal():
                        return 0
                prev = cur
            except Exception:
                log.exception("supervisor tick failed; continuing")
            self._sleep(self.config.poll_interval)
        return 0


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the ``python3 -m kanibako.box_supervisor`` argument parser (options only).

    The trailing ``-- <agent argv>`` is split off BEFORE argparse (see
    :func:`config_from_argv`), so the parser only sees the named options.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m kanibako.box_supervisor",
        description="Box always-on-agent supervisor (PID-1 keep-alive).",
    )
    parser.add_argument("--session", required=True, help="tmux session name for the agent")
    parser.add_argument(
        "--marker", required=True, help="continue-marker sent to a self-healed agent"
    )
    parser.add_argument(
        "--poll", type=float, default=2.0, help="seconds between watch-loop ticks"
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="bounded self-heal restart attempts"
    )
    parser.add_argument(
        "--continue-cmd",
        default=None,
        help="agent grammar (shlex) for a self-heal restart; defaults to the start argv",
    )
    parser.add_argument(
        "--on-agent-exit",
        choices=("self-heal", "teardown"),
        default="self-heal",
        help=(
            "policy when the agent exits: 'self-heal' (default; detached — bounded-retry "
            "restart) or 'teardown' (foreground CLI — close the box on agent exit)"
        ),
    )
    return parser


def config_from_argv(argv: list[str]) -> SupervisorConfig:
    """Parse *argv* (without the program name) into a :class:`SupervisorConfig`.

    Splits on the first standalone ``--``: everything before it is parsed as options,
    everything after is the agent ``start_argv``.  ``--continue-cmd`` is shlex-split
    into ``continue_argv`` (defaulting to a copy of ``start_argv`` when absent).  A
    missing ``--`` / empty trailing argv is an error (there is no agent to run).
    """
    parser = _build_parser()
    if "--" in argv:
        idx = argv.index("--")
        opt_args, start_argv = argv[:idx], argv[idx + 1 :]
    else:
        opt_args, start_argv = list(argv), []
    ns = parser.parse_args(opt_args)
    if not start_argv:
        parser.error("no agent argv given after '--'")
    continue_argv = shlex.split(ns.continue_cmd) if ns.continue_cmd else list(start_argv)
    return SupervisorConfig(
        session=ns.session,
        start_argv=list(start_argv),
        continue_argv=continue_argv,
        marker=ns.marker,
        poll_interval=ns.poll,
        max_restart_retries=ns.max_retries,
        on_agent_exit=ns.on_agent_exit,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: parse args, build the supervisor, run the watch loop forever.

    ``python3 -m kanibako.box_supervisor --session NAME --marker 'STR' [--poll SEC]
    [--max-retries N] [--continue-cmd 'ARGV'] [--on-agent-exit self-heal|teardown]
    -- <agent entrypoint + argv...>``
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config = config_from_argv(args)
    supervisor = BoxSupervisor(config)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
