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
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
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

# The panel-agent liveness probes (E2f), injectable so unit tests never touch the
# real FS / os: ``_PidAlive`` answers "is this PID a live process?" and
# ``_PidfileReader`` reads the marker file's text (``None`` when it is absent /
# unreadable).  Defaults below are the real PID-1 implementations.
_PidAlive = Callable[[int], bool]
_PidfileReader = Callable[[str], "str | None"]


def _default_pid_alive(pid: int) -> bool:
    """Real ``_PidAlive``: is *pid* a live process? (``os.kill(pid, 0)``).

    Shared PID namespace (the supervisor is PID-1, so it sees the panel agent):
    ``os.kill(pid, 0)`` sends no signal but raises when the PID is not a live,
    signalable process.  ``ProcessLookupError`` ⇒ dead; ``PermissionError`` ⇒
    ALIVE (the process exists, we merely may not signal it); any other ``OSError``
    ⇒ treated as not-live (tolerant — the panel-watch caller degrades to "no live
    panel agent" rather than crashing PID-1).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _default_read_pidfile(path: str) -> str | None:
    """Real ``_PidfileReader``: return the marker file's text, or ``None``.

    Tolerant (PID-1 must never die on a missing/racing pidfile): an absent file,
    an unreadable one, or any other ``OSError`` resolves to ``None`` — read by the
    caller as "no panel agent yet" — never an exception.
    """
    try:
        return Path(path).read_text()
    except OSError:
        return None


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
    * *panel_watch* — PANEL-WATCH mode (E2f, design cases 3a/3b): when ``True`` the
      supervisor starts NO CLI agent (the VS Code panel is the agent), watches the
      *agent_pidfile* liveness MARKER + the vscode_server surface, and self-heals a
      CLI agent ONLY when the panel agent DIES with the panel still connected (the
      §89-96 fallback).  ``False`` (default) is the E2b-E2e tmux-agent path,
      byte-unchanged.  This is the ``kanibako code`` AGENT-INDEPENDENT warm-up.
    * *agent_pidfile* — box-local path to the panel-agent liveness marker (the panel
      agent's start hook writes its PID here; E2g).  Read tolerantly by
      :meth:`BoxSupervisor.panel_agent_state`.  Only consulted under *panel_watch*.
    * *creds_flag* — box-local ABSOLUTE path to the credential-writeback SIGNAL flag
      (increment D).  On EVERY detach transition (all modes) :meth:`_on_detach`
      writes this flag into the supervisor's OWN box-home (already host-visible via
      the box-home bind mount), so a TRUSTED HOST watcher (:mod:`kanibako.creds_watcher`)
      can do the privileged box-home → store credential writeback.  The box NEVER
      touches the host credential store itself (the load-bearing trust invariant).
      ``None`` (the default) leaves :meth:`_on_detach` a no-op — an old host launcher
      that threads no flag simply signals nothing.
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
    panel_watch: bool = False
    agent_pidfile: str | None = None
    creds_flag: str | None = None


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
# PANEL-WATCH model (E2f) — the agent-independent `code` warm-up path.
# ---------------------------------------------------------------------------

class PanelAgentState(Enum):
    """Liveness of the PANEL-launched agent, from the marker pidfile (E2f).

    * :data:`NONE` — no marker yet (file absent / empty / unparseable): no panel
      agent has started, OR one exited cleanly and removed its marker.
    * :data:`ALIVE` — the marker names a LIVE process (``os.kill(pid, 0)`` ok).
    * :data:`DEAD` — the marker names a process that is NOT live (a crash left the
      pidfile STALE): the panel agent exited.
    """

    NONE = "none"
    ALIVE = "alive"
    DEAD = "dead"


class PanelActionKind(Enum):
    """What a PANEL-WATCH tick must DO (besides the detach hook)."""

    NONE = "none"
    SELF_HEAL_CLI = "self_heal_cli"
    TEARDOWN = "teardown"


@dataclass(frozen=True)
class PanelAction:
    """The decision a single panel-watch tick produces.

    * *kind* — :data:`PanelActionKind.SELF_HEAL_CLI` when the panel agent died with
      the panel still connected (launch a CLI agent in tmux, the §89-96 fallback);
      :data:`PanelActionKind.TEARDOWN` when every surface + agent is gone
      (ref-count / principle B); otherwise :data:`PanelActionKind.NONE` (keep-alive).
    """

    kind: PanelActionKind = PanelActionKind.NONE


def decide_panel(
    tmux_alive: bool,
    panel: PanelAgentState,
    vscode_server: bool,
    any_attached: bool,
    seen_surface: bool,
) -> PanelAction:
    """PURE: decide a panel-watch tick's action (E2f state machine, design 3a/3b).

    Two DISTINCT surface signals combine (design principle B / the E2e FF-8 fix):

    * *vscode_server* — the PANEL specifically.  It gates SELF_HEAL_CLI, which is the
      panel-specific §89-96 fallback ("the PANEL died while the panel is connected →
      launch a CLI agent").  A tmux terminal is NOT a panel, so it cannot trigger a
      panel-death self-heal.
    * *any_attached* — ANY client surface (panel OR tmux terminal).  It gates the
      ref-count KEEP-ALIVE / TEARDOWN: a box must persist while ANY surface is
      attached, so tearing down keys on "no surface AT ALL is attached" — never on
      the panel alone (else a box could close out from under an attached terminal,
      the exact FF-8-class bug E2e fixed for the CLI path).

    The state machine:

    * ``tmux_alive`` OR ``panel == ALIVE`` → :data:`PanelActionKind.NONE` — an agent
      IS running (a self-healed CLI agent in tmux, or the live panel agent); hands-off.
    * No live agent:

      * ``panel == DEAD`` AND ``vscode_server`` → :data:`PanelActionKind.SELF_HEAL_CLI`
        — the panel agent died but the PANEL is STILL connected, so launch a CLI
        agent in tmux (the §89-96 fallback).  Thereafter a tmux agent exists and the
        first branch keeps it hands-off / self-healed.
      * Else (``panel`` is ``NONE``, or ``DEAD`` with no panel):

        * ``any_attached`` (ANY surface — panel OR terminal — is present) →
          :data:`PanelActionKind.NONE` — keep-alive (principle B: a live surface
          keeps the box up; a panel will (re)bring an agent, a terminal is a human).
        * No surface AND ``seen_surface`` (a surface was present earlier, now gone)
          → :data:`PanelActionKind.TEARDOWN` — ref-count / principle B: ALL surfaces
          and agents are gone, close the box.
        * No surface AND NOT ``seen_surface`` (never attached — a freshly warmed box)
          → :data:`PanelActionKind.NONE` — keep-alive through the STARTUP GRACE so we
          do not tear down before VS Code first attaches.  (Known: a ``code`` box
          that is NEVER attached lingers until ``kanibako stop`` — acceptable.)

    Deterministic and side-effect free over its inputs, so the whole panel-watch
    loop's logic is exhaustively unit-testable without any tmux / FS / os.
    """
    if tmux_alive or panel is PanelAgentState.ALIVE:
        return PanelAction(PanelActionKind.NONE)
    if panel is PanelAgentState.DEAD and vscode_server:
        return PanelAction(PanelActionKind.SELF_HEAL_CLI)
    if any_attached:
        return PanelAction(PanelActionKind.NONE)
    if seen_surface:
        return PanelAction(PanelActionKind.TEARDOWN)
    return PanelAction(PanelActionKind.NONE)


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
        pid_alive: _PidAlive = _default_pid_alive,
        read_pidfile: _PidfileReader = _default_read_pidfile,
    ) -> None:
        self.config = config
        self._run = run
        self._sleep = sleep
        # When provided, a fixed process-cmdline listing handed to
        # snapshot_attach_state (tests inject it to skip the real ``/proc`` walk);
        # ``None`` ⇒ each snapshot collects fresh from ``/proc`` (the real PID-1 path).
        self._proc_cmdlines = None if proc_cmdlines is None else list(proc_cmdlines)
        # Panel-agent liveness probes (E2f), injectable so unit tests never touch the
        # real FS / os; defaults are the real PID-1 implementations.
        self._pid_alive = pid_alive
        self._read_pidfile = read_pidfile
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

    def _other_surface_attached(self, state: AttachState) -> bool:
        """True when a surface OTHER than the foreground CLI's own terminal is attached.

        The CLI↔panel REF-COUNT SLICE (E2e, design principle B): a FOREGROUND launch's
        OWN surface is the tmux TERMINAL it attached, so the "other" surface whose
        presence must keep the box alive AFTER that CLI agent exits is the VS Code
        PANEL — :attr:`AttachState.vscode_server`.  While the panel is attached, an
        agent exit stays an agentless keep-alive (the box persists for the panel)
        instead of tearing the box down; the box closes only once this last other
        surface ALSO detaches — a poll-based ref-count where the box stops when the
        LAST surface goes.

        Deliberately the CLI↔panel slice, not a full N-terminal ref-count (multiple
        independent CLI terminals) — that generalization is a noted extension (E2e
        brief, "Out of scope"); this slice covers the stated FF-8 bug (a CLI agent
        exit must not demolish a box a panel is concurrently using).  Reads only the
        already-probed :class:`AttachState`, so it is as tolerant as the snapshot.
        """
        return state.vscode_server

    # -- panel-agent liveness (E2f) ------------------------------------------

    def panel_agent_state(self) -> PanelAgentState:
        """Liveness of the PANEL-launched agent from the marker pidfile (E2f).

        Reads ``config.agent_pidfile`` via the injected reader and checks the PID
        via the injected liveness probe (defaults: real FS read + ``os.kill(pid,
        0)``).  TOLERANT throughout — PID-1 must never die on a bad marker:

        * no ``agent_pidfile`` configured, file absent / empty / unparseable, or a
          probe that raises → :data:`PanelAgentState.NONE` ("no live panel agent");
        * a parseable positive PID that is a LIVE process → :data:`PanelAgentState.ALIVE`;
        * a parseable positive PID that is NOT live (a stale marker a crash left
          behind) → :data:`PanelAgentState.DEAD`.
        """
        path = self.config.agent_pidfile
        if not path:
            return PanelAgentState.NONE
        try:
            raw = self._read_pidfile(path)
        except Exception:
            log.debug("panel_agent_state: pidfile read raised for %r; treating as NONE", path)
            return PanelAgentState.NONE
        if not raw or not raw.strip():
            return PanelAgentState.NONE
        try:
            pid = int(raw.strip())
        except ValueError:
            log.debug("panel_agent_state: unparseable pidfile contents %r", raw)
            return PanelAgentState.NONE
        if pid <= 0:
            return PanelAgentState.NONE
        try:
            alive = self._pid_alive(pid)
        except Exception:
            log.debug("panel_agent_state: liveness probe raised for pid %d; treating as NONE", pid)
            return PanelAgentState.NONE
        return PanelAgentState.ALIVE if alive else PanelAgentState.DEAD

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
        """Best-effort HOOK fired on a DETACH tick — write the creds-dirty SIGNAL flag.

        Increment D (GAP-1 credential writeback): a client detached, so an in-box
        panel/agent may have refreshed a shared credential.  The load-bearing TRUST
        invariant is that the box NEVER writes the host credential store (a mount is
        not process-scoped → the untrusted agent would inherit any store-write
        handle).  So the supervisor only SIGNALS: it edge-triggers a flag in its OWN
        box-home (``config.creds_flag``, already host-visible via the box-home bind
        mount), and a TRUSTED HOST watcher (:mod:`kanibako.creds_watcher`) does the
        privileged box-home → store copy via the existing host writeback.

        Universal across supervisor modes (foreground teardown, detached self-heal,
        panel-watch) — the flag means only "a client detached, creds may have
        refreshed", which the host resolves (writeback is a no-op for a private box).
        Best-effort and idempotent: ``None`` flag ⇒ no-op (an old launcher threading
        no ``--creds-flag``); a missing parent dir is created; ANY ``OSError`` is
        swallowed here (and the loop also calls this via :meth:`_safe_on_detach`, so
        even an unexpected raise can never break the supervisor).
        """
        path = self.config.creds_flag
        if not path:
            log.debug("on-detach: no creds-flag configured; nothing to signal")
            return
        try:
            flag = Path(path)
            flag.parent.mkdir(parents=True, exist_ok=True)
            # A tiny edge-trigger MARKER — the host watcher only checks EXISTENCE, so
            # the contents are immaterial; a single byte keeps it a non-empty file.
            flag.write_text("1")
            log.debug("on-detach: wrote creds-dirty flag %s", path)
        except OSError as exc:
            log.debug("on-detach: could not write creds-dirty flag %r: %s", path, exc)

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
          termination, but SURFACE-AWARE (E2e, principle B / ref-count): PID-1 closes
          the box ONLY when no OTHER client surface is still attached.  With no other
          surface, return the AGENT's own exit code (from the dead pane's
          ``#{pane_dead_status}``; 0 for a clean exit, 1 when dead-but-unknown) so
          PID-1 exits with a TRUTHFUL code and the box closes — a supervised agent
          CRASH surfaces via the host path instead of masquerading as success (E2d).
          But when a VS Code PANEL is attached (:meth:`_other_surface_attached`), do
          NOT tear down: stay an AGENTLESS keep-alive (the box persists for the
          panel; do NOT self-heal a CLI agent while the panel is the live surface —
          the one-agent invariant) and keep polling, closing on a LATER tick once
          that last surface also detaches.  No self-heal loop while a CLI is the
          driver.  A failed INITIAL start is handled UNIFORMLY by the loop's first
          tick (below), so it too is surface-aware.

        Returns a process exit code.
        """
        self.install_signal_handlers()
        if self.config.panel_watch:
            # E2f: the `code` AGENT-INDEPENDENT warm-up runs a distinct loop that
            # starts NO CLI agent and watches the panel-agent marker + surface.  The
            # E2b-E2e path below is untouched (only reached when NOT panel_watch).
            return self._run_panel_watch()
        teardown_on_exit = self.config.on_agent_exit == "teardown"
        # One-shot guard so the agentless keep-alive state (E2e: agent dead but a
        # panel keeps the box up) logs ONCE on entry, not per poll tick.
        keepalive_announced = False
        # Startup runs BEFORE the per-tick guard, so guard it too: a probe raising
        # here (e.g. an unexpected snapshot failure) must not kill PID-1 before the
        # loop even begins.  On any startup hiccup, degrade to "no attach" and enter
        # the loop — it self-heals (or, under teardown, closes on) a missing agent on
        # its first tick.
        try:
            if not self.agent_session_alive():
                log.info("no live agent session at startup; starting one detached")
                if not self.start_agent_session():
                    # A failed INITIAL start is handled UNIFORMLY by the loop's first
                    # tick (E2e factoring): under 'self-heal' the loop self-heals a
                    # missing agent; under 'teardown' the loop's SURFACE-AWARE branch
                    # closes the box (rc from the dead pane, 1 when unknown) UNLESS a
                    # panel is attached — in which case it stays up as an agentless
                    # keep-alive.  No special-case return here (one start attempt, then
                    # the loop's ref-count policy decides) keeps the teardown decision
                    # in exactly ONE place (design §86-88, cold-start-error-human-direct).
                    log.warning("initial agent start failed; deferring to the loop policy")
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
                        # SURFACE-AWARE teardown (E2e, principle B / ref-count): the
                        # agent is dead, but a box must PERSIST while any OTHER client
                        # surface (a VS Code panel) is still attached — an FF-8 bug is
                        # a CLI agent exit demolishing a box a panel is using.
                        if self._other_surface_attached(cur):
                            # Stay an AGENTLESS keep-alive: do NOT tear down, and do
                            # NOT self-heal a CLI agent while the panel is the live
                            # surface (the one-agent invariant).  Log ONCE on entry
                            # (guard the per-tick spam), then fall through to keep
                            # polling; a LATER tick tears down once the panel detaches.
                            if not keepalive_announced:
                                log.info(
                                    "agent exited but another client surface (panel) "
                                    "is attached; staying up as an agentless keep-alive"
                                )
                                keepalive_announced = True
                        else:
                            # No other surface → today's E2d teardown: propagate the
                            # agent's TRUE exit code as PID-1's own, so the container's
                            # exit code (and thus the host's foreground error handling)
                            # is TRUTHFUL — a supervised agent CRASH surfaces instead of
                            # masquerading as a clean exit.  Read the dead pane's
                            # ``#{pane_dead_status}``; a clean status-0 exit ⇒ 0, and a
                            # dead-but-unknown agent (status unreadable, or the session
                            # vanished entirely) ⇒ 1 — a failure, not a success.
                            status = self.agent_pane_dead_status()
                            code = 1 if status is None else status
                            log.info(
                                "agent exited under teardown policy (dead_status=%s); "
                                "no other surface attached — closing box with rc %d",
                                status,
                                code,
                            )
                            return code
                    elif not self._self_heal():
                        return 0
                prev = cur
            except Exception:
                log.exception("supervisor tick failed; continuing")
            self._sleep(self.config.poll_interval)
        return 0

    def _run_panel_watch(self) -> int:
        """The PANEL-WATCH loop (E2f): agent-independent ``code`` warm-up.

        Unlike :meth:`run_forever`'s tmux-agent path, startup starts NO CLI agent —
        the VS Code panel is the agent.  Each tick snapshots the surfaces, tracks a
        ``seen_surface`` LATCH (set once any surface has ever been attached — so a
        never-attached freshly warmed box stays up through the startup grace), and
        drives the pure :func:`decide_panel` over (tmux liveness, panel-agent marker,
        vscode_server, seen_surface):

        * :data:`PanelActionKind.SELF_HEAL_CLI` → the panel agent DIED with the panel
          still connected: run :meth:`_self_heal` (continue grammar + marker) to
          launch a CLI agent in tmux.  Thereafter that tmux agent is live, so
          ``decide_panel`` returns NONE and the loop leaves it be (self-healing it
          again if IT later dies while the panel is up).  A self-heal that EXHAUSTS
          its retries returns 0 (principle B: let the box stop).
        * :data:`PanelActionKind.TEARDOWN` → every surface + agent is gone: return 0.
        * :data:`PanelActionKind.NONE` → keep-alive; keep polling.

        The DETACH hook (:meth:`_safe_on_detach`, D's cred-writeback point) still
        fires on a DETACH transition, computed exactly as the E2b loop does via
        :func:`classify_transition` over the prev→cur snapshot.  Every tick's body is
        guarded so a raising probe/action is logged and the loop CONTINUES (PID-1 must
        not die on a transient error).
        """
        log.info("panel-watch mode: agentless keep-alive fronting the VS Code panel")
        # ``seen_surface`` LATCHES True once any surface has ever been attached, so a
        # box that IS attached and later fully detaches tears down (ref-count), while
        # a never-yet-attached box stays up through the startup grace.  The pre-loop
        # snapshot is guarded like run_forever's startup (a raise must not kill PID-1).
        seen_surface = False
        try:
            prev = self._snapshot()
            if prev.any_attached:
                seen_surface = True
        except Exception:
            log.exception("panel-watch startup snapshot raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                cur = self._snapshot()
                if cur.any_attached:
                    seen_surface = True
                if classify_transition(prev, cur) is LifecycleEvent.DETACH:
                    self._safe_on_detach()
                tmux_alive = self.agent_session_alive()
                panel = self.panel_agent_state()
                action = decide_panel(
                    tmux_alive, panel, cur.vscode_server, cur.any_attached, seen_surface,
                )
                if action.kind is PanelActionKind.SELF_HEAL_CLI:
                    log.info(
                        "panel agent died with the panel still connected; "
                        "self-healing a CLI agent in tmux (the §89-96 fallback)"
                    )
                    if not self._self_heal():
                        return 0
                elif action.kind is PanelActionKind.TEARDOWN:
                    log.info(
                        "all client surfaces and agents gone; "
                        "tearing down the warmed panel-watch box"
                    )
                    return 0
                prev = cur
            except Exception:
                log.exception("panel-watch tick failed; continuing")
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
    parser.add_argument(
        "--panel-watch",
        action="store_true",
        help=(
            "PANEL-WATCH mode (E2f): start NO CLI agent; watch the panel-agent marker "
            "(--agent-pidfile) + the VS Code server surface and self-heal a CLI agent "
            "only when the panel agent dies with the panel still connected"
        ),
    )
    parser.add_argument(
        "--agent-pidfile",
        default=None,
        help="box-local path to the panel-agent liveness marker (read under --panel-watch)",
    )
    parser.add_argument(
        "--creds-flag",
        default=None,
        help=(
            "box-local path to the credential-writeback SIGNAL flag (increment D): "
            "written on EVERY detach so a trusted HOST watcher does the privileged "
            "store writeback; omit to signal nothing"
        ),
    )
    return parser


def config_from_argv(argv: list[str]) -> SupervisorConfig:
    """Parse *argv* (without the program name) into a :class:`SupervisorConfig`.

    Splits on the first standalone ``--``: everything before it is parsed as options,
    everything after is the agent ``start_argv``.  ``--continue-cmd`` is shlex-split
    into ``continue_argv`` (defaulting to a copy of ``start_argv`` when absent).  A
    missing ``--`` / empty trailing argv is an error (there is no agent to run) —
    EXCEPT under ``--panel-watch`` (E2f), which starts NO agent at launch, so it
    takes an empty ``start_argv`` and relies on ``--continue-cmd`` for its self-heal
    grammar (the host always threads one through).
    """
    parser = _build_parser()
    if "--" in argv:
        idx = argv.index("--")
        opt_args, start_argv = argv[:idx], argv[idx + 1 :]
    else:
        opt_args, start_argv = list(argv), []
    ns = parser.parse_args(opt_args)
    if not start_argv and not ns.panel_watch:
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
        panel_watch=ns.panel_watch,
        agent_pidfile=ns.agent_pidfile,
        creds_flag=ns.creds_flag,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: parse args, build the supervisor, run the watch loop forever.

    ``python3 -m kanibako.box_supervisor --session NAME --marker 'STR' [--poll SEC]
    [--max-retries N] [--continue-cmd 'ARGV'] [--on-agent-exit self-heal|teardown]
    [--panel-watch --agent-pidfile PATH] [--creds-flag PATH]
    -- <agent entrypoint + argv...>``

    In ``--panel-watch`` mode (E2f) the trailing ``-- <agent argv>`` is OMITTED (no
    agent starts at launch); ``--continue-cmd`` carries the self-heal grammar.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config = config_from_argv(args)
    supervisor = BoxSupervisor(config)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
