"""Box always-on-agent SUPERVISOR — the PID-1 keep-alive that outlives sessions.

The always-on-instance design (`split-brain-persistence-DESIGN.md`, "E2 BUILD
DESIGN") makes a box's keep-alive PID-1 a SUPERVISOR: it runs the agent in a
DETACHED tmux session, watches client attach/detach via :mod:`kanibako.box_lifecycle`,
and SELF-HEALS the agent (restart with `--continue` + a continue-marker) when it
dies — so the box persists independent of any one agent session (design principle
B: only genuine exit-of-everything, or an explicit ``kanibako stop``, tears the box
down).

This module grew from INCREMENT 2 (E2a) — the supervisor MODULE — through E2b-E2h
(the launch-model wiring in ``start.py``), increment 4a (single-state DETECTION), and
increment 4b (single-writer ENFORCEMENT).  4a enumerates the per-agent markers dir
(:func:`scan_marker_pids`) and flags any live marker PID that is NOT the supervisor's
own agent (:func:`newcomer_pids`) — a second agent has bound the session (the
split-brain hazard).  4b adds the TAKEOVER (:meth:`BoxSupervisor._takeover`) for the
CRITICAL direction (a VS Code panel newcomer taking over a live CLI incumbent in
``run_forever``): PAUSE the newcomer (``SIGSTOP``), a best-effort GRACE + heads-up to
the incumbent, then EVICT the incumbent as a PROCESS GROUP (:meth:`kill_agent_session`,
child-kill-with-parent so no orphaned subagent survives), then RESUME the newcomer
(``SIGCONT``) as the sole writer + hand off to the agentless panel-watch keep-alive.

⚑ HARD SAFETY GATE: the DESTRUCTIVE evict is gated behind ``config.session_takeover``
(threaded from the experimental env ``KANIBAKO_SESSION_TAKEOVER``), DEFAULT-OFF so 4b's
NEWCOMER TAKEOVER lands DORMANT — when OFF, both loops handle a newcomer exactly as 4a
(LOG-ONLY: no SIGSTOP, no send-keys, no eviction of a newcomer).  ⚑ NOTE: the
process-GROUP kill in :meth:`kill_agent_session` runs on the normal TEARDOWN and
self-heal-restart paths REGARDLESS of the flag (a strict improvement — it reaps orphaned
subagents that a bare ``tmux kill-session`` would leave); it is guarded against a
pathological ``pgid`` (never signals pgid ``<=1`` or the supervisor's own group).  The
evict TARGET is always the pane incumbent (:meth:`_own_agent_pids`, CLI-side
validated), never a bare marker PID.  The REVERSE direction (a CLI newcomer over a
live-panel incumbent, in panel-watch) has no panel injection vector → it stays
LOG-ONLY (deferred).  :meth:`kill_agent_session` is BOTH the total-teardown kill and
the 4b eviction primitive (the process-group handling its docstring reserved).

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
from collections.abc import Callable, Iterable, MutableMapping
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

#: The heads-up string 4b ENFORCEMENT delivers (via ``tmux send-keys``) to a CLI
#: INCUMBENT that is about to be evicted by a panel newcomer taking over the session
#: (design ``split-brain-persistence-DESIGN.md`` §346, "GRACE + heads-up to the
#: incumbent").  It lands as a real acting turn if the incumbent is idle, or queues
#: mid-loop — a best-effort nudge to wind down/checkpoint before the bounded grace
#: window elapses and the incumbent's process group is evicted.  Injection is feasible
#: ONLY toward the tmux (CLI) agent; a panel incumbent has no injection vector (the
#: DIRECTIONAL limit), so this is used only in the CRITICAL direction.
TAKEOVER_HEADS_UP = (
    "[Session takeover - another surface is taking over this session; "
    "wind down and checkpoint now if you have work in progress]"
)

#: In-box read-only bind-mount ROOT of the HOST kanibako package.  ``_kanibako_mounts``
#: (``commands/start.py``) lands the host package dir at ``f"{KANIBAKO_PKG_MOUNT_ROOT}/
#: kanibako"`` and the in-box ``kanibako`` CLI puts this dir on ``sys.path``; the host
#: launcher (``_build_supervisor_pid1``) also injects it as ``PYTHONPATH`` so PID-1
#: imports THIS supervisor from the fresh host package (version == host CLI) rather
#: than the image's baked copy — published images ship an OLD kanibako WITHOUT the
#: supervisor module, so importing the baked copy would silently degrade every real
#: launch to the bare-shell fallback.  Single-sourced HERE (the lowest module that
#: needs it: ``start.py`` imports it, and :func:`scrub_bootstrap_pythonpath` strips it
#: back out) so the mount dest and the injected/scrubbed PYTHONPATH can never drift.
#: NOTE: ``scripts/kanibako-entry`` and ``data/core-defaults.yaml`` also carry this
#: literal (the entry script runs BEFORE kanibako is importable, and a YAML data
#: file cannot reference a constant); keep those literals in sync with this value.
KANIBAKO_PKG_MOUNT_ROOT = "/opt/kanibako"


def scrub_bootstrap_pythonpath(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Strip the injected :data:`KANIBAKO_PKG_MOUNT_ROOT` entry from PYTHONPATH.

    The host launcher prepends :data:`KANIBAKO_PKG_MOUNT_ROOT` to ``PYTHONPATH`` so
    PID-1 imports the supervisor from the fresh host-mounted kanibako, not the image's
    baked copy.  But the supervisor's CHILDREN — the tmux server and, under it, the
    AGENT — must NOT inherit that entry: the agent runs with its own environment and
    has no business importing kanibako from our host mount (an inherited
    ``/opt/kanibako`` on the agent's path could also shadow an unrelated package in
    the agent's own tree).  ``PYTHONPATH`` is read by CPython only at interpreter
    STARTUP, so mutating the env now cannot disturb THIS already-running supervisor's
    ``sys.path`` — it affects only processes spawned AFTER this call.  Call it once,
    before the watch loop spawns anything.

    Removes EXACTLY the mount-root path element, preserving every other entry the
    image set, and drops ``PYTHONPATH`` entirely when nothing else remains.  Defaults
    to :data:`os.environ` (the real child-inheritance source); a caller may pass an
    explicit mapping (tests).
    """
    env = os.environ if environ is None else environ
    raw = env.get("PYTHONPATH")
    if raw is None:
        return
    kept = [p for p in raw.split(os.pathsep) if p != KANIBAKO_PKG_MOUNT_ROOT]
    if kept:
        env["PYTHONPATH"] = os.pathsep.join(kept)
    else:
        env.pop("PYTHONPATH", None)


# The subprocess-runner signature the tmux actions call.  ``subprocess.run``
# matches it; tests inject a fake so nothing touches a real tmux server.  Shared
# in spirit with box_lifecycle's ``_Runner``.
_Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# The ``time.sleep`` signature the loop / backoff use; injectable so tests never
# actually wait.
_Sleeper = Callable[[float], None]

# The agent-marker probes (E2f liveness + 4a detection), injectable so unit tests
# never touch the real FS / os: ``_PidAlive`` answers "is this PID a live process?"
# and ``_MarkersLister`` returns the PIDs named by the marker FILES in the markers dir
# (``[]`` when the dir is absent / empty).  Defaults below are the real PID-1
# implementations.
_PidAlive = Callable[[int], bool]
_MarkersLister = Callable[[str], "list[int]"]


def _parse_stat_state(stat_text: str) -> str | None:
    """PURE: the process STATE character from a ``/proc/<pid>/stat`` line.

    The stat line is ``<pid> (<comm>) <state> <ppid> …`` — and ``comm`` is
    attacker/filename-controlled text that may itself contain spaces AND
    parentheses (e.g. a process named ``a) (b``), so a naive ``split()`` is
    wrong.  The kernel emits ``comm`` as the ONLY parenthesised field, so the
    state is the first token AFTER the **last** ``)``.  Returns ``None`` when
    the text has no ``)`` or nothing after it (unparseable — the caller falls
    back to its kill-0 verdict).
    """
    _, sep, tail = stat_text.rpartition(")")
    if not sep:
        return None
    fields = tail.split()
    return fields[0] if fields else None


def _proc_stat_state(pid: int) -> str | None:
    """The kernel state char for *pid* from ``/proc/<pid>/stat``, or ``None``.

    ``None`` on ANY read/parse problem (proc entry raced away, no /proc, …) —
    tolerant; the caller falls back to its kill-0 verdict, never raises.
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return _parse_stat_state(text)


def _default_pid_alive(pid: int) -> bool:
    """Real ``_PidAlive``: is *pid* a live (non-zombie) process?

    Shared PID namespace (the supervisor is PID-1, so it sees the panel agent).
    Two layers:

    * EXISTENCE — ``os.kill(pid, 0)`` sends no signal but raises when the PID
      is not a signalable process.  ``ProcessLookupError`` ⇒ dead;
      ``PermissionError`` ⇒ exists (we merely may not signal it); any other
      ``OSError`` ⇒ treated as not-live (tolerant — the caller degrades to
      "not a live agent" rather than crashing PID-1).
    * ⚑ ZOMBIE — ``kill -0`` answers True for a ZOMBIE (an exited child not
      yet ``wait()``ed by its parent — and orphaned processes reparent to the
      supervisor itself, PID-1, so an unreaped panel agent would read ALIVE
      **forever** and wedge both SELF_HEAL_CLI and TEARDOWN).  So an existing
      PID is additionally checked against ``/proc/<pid>/stat``: state ``Z`` ⇒
      dead.  An unreadable/unparseable stat (``None``) falls back to the
      kill-0 verdict (True here) — never raises.  See also
      :func:`reap_zombie_children`, the PID-1 duty that clears the zombie
      itself.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return _proc_stat_state(pid) != "Z"


def reap_zombie_children(max_reaps: int = 32) -> int:
    """Bounded non-blocking reap of this process's exited children (PID-1 duty).

    The supervisor is the box's PID-1, so every ORPHANED in-box process (e.g. a
    panel-spawned agent whose own parent already exited) reparents to it — and
    on exit sits as a ZOMBIE until PID-1 ``wait()``s it.  Without reaping, the
    zombie both leaks a process-table slot and (before the
    :func:`_default_pid_alive` zombie check) blinded the liveness probe.

    Reaping our OWN children is always safe here regardless of PID-1-ness: the
    supervisor collects no child exit statuses anywhere else (its only child
    mechanism is inline ``subprocess.run``, which waits its child synchronously
    on the same single thread — there is no concurrent waiter to rob), so both
    supervise loops call this unconditionally at the top of every tick.

    BOUNDED to *max_reaps* per call so a burst of zombies cannot stall a tick
    (the next tick continues the drain).  ``ChildProcessError`` (no children at
    all) ends the sweep; any other ``OSError`` is swallowed — PID-1's tick must
    never die here.  Returns the number reaped.
    """
    reaped = 0
    for _ in range(max_reaps):
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except OSError:
            log.debug("reap_zombie_children: waitpid raised; ending sweep", exc_info=True)
            break
        if pid == 0:  # children exist but none exited — done this tick
            break
        reaped += 1
        log.debug("reaped exited child pid %d", pid)
    return reaped


def _default_list_marker_pids(markers_dir: str) -> list[int]:
    """Real ``_MarkersLister``: the PIDs named by the marker FILES in *markers_dir*.

    Each live agent session's start hook writes a per-PID marker FILE named for its
    ``$PPID`` (``<dir>/<pid>``; :data:`kanibako.vscode.vscode_config.AGENT_MARKERS_DIR`), so
    the FILENAMES enumerate the agent PIDs — no file READ is needed.  Parses each
    entry name as an ``int``, skipping any non-integer name.  Tolerant (PID-1 must
    never die on a missing/racing dir): an absent dir or any ``OSError`` resolves to
    ``[]`` — read by the caller as "no agents yet" — never an exception.
    """
    try:
        names = os.listdir(markers_dir)
    except OSError:
        return []
    pids: list[int] = []
    for name in names:
        try:
            pids.append(int(name))
        except ValueError:
            continue
    return pids


def scan_marker_pids(
    markers_dir: str,
    *,
    list_pids: _MarkersLister,
    pid_alive: _PidAlive,
) -> tuple[set[int], set[int]]:
    """PURE-ish: partition the markers dir into (LIVE pids, STALE pids).

    Enumerates the marker PIDs via *list_pids*, drops any non-positive PID, then
    prunes-dead via *pid_alive*: a marker whose process is live lands in the LIVE
    set, one whose process is gone (a crash left the marker behind) lands in the
    STALE set.  A *pid_alive* probe that RAISES for a given PID is swallowed and that
    PID skipped (tolerant — one bad probe must not crash PID-1).  Deterministic over
    its injected probes, so tests exhaust it with no real FS / os.
    """
    live: set[int] = set()
    stale: set[int] = set()
    for pid in list_pids(markers_dir):
        if pid <= 0:
            continue
        try:
            alive = pid_alive(pid)
        except Exception:
            log.debug("scan_marker_pids: liveness probe raised for pid %d; skipping", pid)
            continue
        (live if alive else stale).add(pid)
    return live, stale


def newcomer_pids(live_pids: set[int], own_pids: set[int]) -> set[int]:
    """PURE: the LIVE marker PIDs that are NOT the supervisor's OWN agent.

    A newcomer is a second agent that has bound the session — a live marker whose PID
    the supervisor did not launch/front (the split-brain hazard the design's
    increment 4 catches).  *own_pids* is the mode-specific set of legitimate agent
    PIDs (run_forever: the tmux pane PID(s); panel-watch: the tmux pane PID(s) of a
    self-healed CLI PLUS the fronted panel incumbent — see the loops).  4a only LOGS
    these; 4b will act.
    """
    return live_pids - own_pids


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
    * *session_takeover* — 4b ENFORCEMENT master switch (design §338-359), DEFAULT-OFF
      so 4b lands DORMANT: ``False`` (unset) ⇒ the run_forever loop's newcomer handling
      is 4a LOG-ONLY (no SIGSTOP / send-keys / newcomer eviction — the newcomer path is
      4a-identical; note the teardown/self-heal process-group kill is flag-independent);
      ``True`` ⇒
      full single-writer TAKEOVER (pause the panel newcomer, grace + evict the CLI
      incumbent, resume the newcomer).  Gated behind an internal/experimental env
      (``KANIBAKO_SESSION_TAKEOVER``, threaded by ``commands/start.py``), NOT a spec
      settings key — flipping the default to ``True`` is a follow-up gated on desktop
      validation of the ``$PPID`` == agent-PID / panel invariant, and promoting it to a
      proper ``agent.default.*`` key is a later spec-delta.
    * *takeover_grace* — seconds the takeover waits (after pausing the newcomer + a
      best-effort heads-up) for the CLI incumbent to wind down before the process-group
      evict.  Kept SHORT by default (the paused panel's stdio blocks while stopped, so a
      long pause risks the extension's watchdog respawning it — a desktop-gated unknown).
    * *panel_watch* — PANEL-WATCH mode (E2f, design cases 3a/3b): when ``True`` the
      supervisor starts NO CLI agent (the VS Code panel is the agent), enumerates the
      *agent_markers_dir* liveness MARKERS + the vscode_server surface, and self-heals
      a CLI agent ONLY when the panel agent DIES with the panel still connected (the
      §89-96 fallback).  ``False`` (default) is the E2b-E2e tmux-agent path,
      byte-unchanged EXCEPT the LOG-ONLY 4a newcomer detection.  This is the
      ``kanibako code`` AGENT-INDEPENDENT warm-up.
    * *agent_markers_dir* — box-local per-agent liveness MARKERS directory (each agent
      session's start hook writes a per-PID marker ``<dir>/$PPID``; E2g / increment
      4a).  Enumerated tolerantly by :meth:`BoxSupervisor.panel_agent_state` (panel
      liveness) and by the LOG-ONLY 4a newcomer detection wired into BOTH loops.
      ``None`` (default) disables both (an old launcher that threads no dir).
    * *creds_flag* — box-local ABSOLUTE path to the credential-writeback SIGNAL flag
      (increment D).  On EVERY detach transition (all modes) :meth:`_on_detach`
      writes this flag into the supervisor's OWN box-home (already host-visible via
      the box-home bind mount), so a TRUSTED HOST watcher (:mod:`kanibako.launch.creds_watcher`)
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
    session_takeover: bool = False
    takeover_grace: float = 5.0
    panel_watch: bool = False
    agent_markers_dir: str | None = None
    creds_flag: str | None = None
    # Bounded scrollback (lines) captured from the dead agent pane on a foreground
    # teardown and echoed to PID-1's stdout so ``podman logs`` surfaces the agent's
    # final output to the host (:meth:`BoxSupervisor.capture_agent_output`).
    capture_history: int = 200


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
    """Liveness of the PANEL-launched agent, from the per-PID markers dir (E2f).

    Computed from the NON-OWN markers (excluding a self-healed CLI's own tmux pane):

    * :data:`NONE` — no non-own marker (dir absent, or every panel agent exited
      cleanly and removed its ``<dir>/$PPID``): no panel agent is present.
    * :data:`ALIVE` — a non-own marker names a LIVE process (``os.kill(pid, 0)`` ok).
    * :data:`DEAD` — no live non-own marker but a STALE one remains (a crash left the
      per-PID file behind): the panel agent exited.
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
        list_marker_pids: _MarkersLister = _default_list_marker_pids,
    ) -> None:
        self.config = config
        self._run = run
        self._sleep = sleep
        # When provided, a fixed process-cmdline listing handed to
        # snapshot_attach_state (tests inject it to skip the real ``/proc`` walk);
        # ``None`` ⇒ each snapshot collects fresh from ``/proc`` (the real PID-1 path).
        self._proc_cmdlines = None if proc_cmdlines is None else list(proc_cmdlines)
        # Agent-marker probes (E2f liveness + 4a detection), injectable so unit tests
        # never touch the real FS / os; defaults are the real PID-1 implementations.
        self._pid_alive = pid_alive
        self._list_marker_pids = list_marker_pids
        # 4a: PIDs already logged as newcomers, so the LOG-ONLY detection announces a
        # given newcomer ONCE (not every poll tick).  Pruned to the still-present
        # newcomer set each tick, so a departed-then-returning PID re-logs.
        self._reported_newcomers: set[int] = set()
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

    def _start_session_argv(self, session_argv: list[str]) -> list[str]:
        """tmux argv that arms ``remain-on-exit``, starts the detached session, and SCOPES the arm.

        A SINGLE tmux invocation runs four commands in order:

          1. ``set-option -g remain-on-exit on`` — the server-global default, set
             BEFORE the pane exists so it is effective the instant the agent pane is
             born.  This is what lets an INSTANT-crashing agent still leave a
             capturable DEAD pane (real exit code in ``#{pane_dead_status}``, final
             output in scrollback).  Arming AFTER a bare ``new-session -- <agent>``
             (the pre-fix approach) loses the race essentially always — empirically
             0/20 instant crashes left a dead pane — while this arm-before-birth won
             20/20 on real tmux.
          2. ``new-session -d -s <session> -- <agent>`` — ``-d`` detached (agent
             executing, no client); ``--`` ends new-session's option parsing so the
             agent grammar is verbatim.
          3. ``set-option -t <session> remain-on-exit on`` — pin the option SESSION-
             LOCAL so it outlives step 4.
          4. ``set-option -g remain-on-exit off`` — REVERT the global default.  So
             the global is ``on`` only across the pane's birth (steps 1→3); any OTHER
             window sharing this box's tmux server (a user's own ``tmux`` windows, a
             VS Code integrated terminal running tmux) keeps the normal default and
             does NOT accumulate lingering dead panes.  At every instant the AGENT
             pane is covered — by the global (steps 2→3) then by its session-local
             value (after step 3) — so there is no gap (verified: 20/20 instant
             crashes still captured; a sibling session's pane is destroyed normally).

        All four MUST share one invocation: a separate ``set-option -g`` on a
        not-yet-running server starts a server that immediately exits (no session),
        so the global would be gone before ``new-session`` runs.  Each ``;`` is a
        standalone argument — tmux's command separator.
        """
        return [
            "set-option", "-g", "remain-on-exit", "on", ";",
            "new-session", "-d", "-s", self.config.session, "--", *session_argv, ";",
            "set-option", "-t", self.config.session, "remain-on-exit", "on", ";",
            "set-option", "-g", "remain-on-exit", "off",
        ]

    def _arm_and_start_session(self, session_argv: list[str]) -> int | None:
        """Arm remain-on-exit + start the detached session; return the tmux rc.

        SOLE / FOREGROUND launch (``on_agent_exit == "teardown"``): do NOT arm
        ``remain-on-exit`` at all — start a PLAIN detached ``new-session`` so the
        agent pane CLOSES on ANY exit (clean or crash).  With no lingering dead
        pane, an attached foreground client returns straight to the shell instead
        of stranding the user on tmux's "Pane is dead" overlay.  A teardown launch
        never self-heals, so the dead-pane capture / exit-code machinery it would
        otherwise arm is not needed (the human saw the agent's output live).

        SELF-HEAL / detached / panel launch: arm ``remain-on-exit`` as before so a
        dead agent leaves a capturable dead pane for the restart + output capture.
        Normally ONE combined invocation (:meth:`_start_session_argv`).  GUARD: a
        standalone ``;`` token in the agent argv would be read by tmux as a top-level
        command separator in that combined form (``--`` ends new-session's OWN option
        parsing, not tmux's command splitting), so it would break the launch.  That
        essentially never occurs in a real agent grammar, but to avoid making a rare
        input WORSE than the pre-fix behavior, fall back to a plain ``new-session``
        then a best-effort per-session arm — LAUNCH stays correct; only the
        instant-crash dead-pane capture is not guaranteed for this edge.
        """
        if self.config.on_agent_exit == "teardown":
            # No remain-on-exit: the pane closes on exit, no dead pane ever.
            return self._run_tmux(
                ["new-session", "-d", "-s", self.config.session, "--", *session_argv]
            )
        if ";" not in session_argv:
            return self._run_tmux(self._start_session_argv(session_argv))
        rc = self._run_tmux(
            ["new-session", "-d", "-s", self.config.session, "--", *session_argv]
        )
        if rc == 0:
            self._run_tmux(
                ["set-option", "-t", self.config.session, "remain-on-exit", "on"]
            )
        return rc

    def start_agent_session(self) -> bool:
        """Start the agent DETACHED with ``remain-on-exit`` armed globally first.

        See :meth:`_start_session_argv` for why the arm+start share one invocation.
        Returns ``True`` on rc 0.
        """
        rc = self._arm_and_start_session(self.config.start_argv)
        if rc != 0:
            log.warning("start_agent_session: tmux new-session rc=%s", rc)
            return False
        return True

    def restart_agent_session(self) -> bool:
        """Restart the agent for self-heal, with the CONTINUE grammar + marker.

        Arms ``remain-on-exit`` globally and starts ``new-session -d -s <session> --
        <continue_argv>`` in one invocation (:meth:`_start_session_argv`; the
        ``--continue`` form re-reads the box's ``~/.claude`` history), then delivers
        the continue-marker via :meth:`_send_marker` so the successor gets it as a
        REAL acting turn (autonomous resume, no human needed).  Returns ``True`` when
        the new-session started (rc 0); marker delivery is best-effort and logged.

        With ``remain-on-exit`` a dead agent leaves its session PRESENT (a dead pane)
        still holding the canonical name, so a fresh ``new-session`` with the same
        name would COLLIDE.  Kill the (dead) session FIRST — tolerant no-op when
        nothing is there — so the restart can reuse the ``kanibako`` name.
        """
        self.kill_agent_session()
        rc = self._arm_and_start_session(self.config.continue_argv)
        if rc != 0:
            log.warning("restart_agent_session: tmux new-session rc=%s", rc)
            return False
        if not self._send_marker():
            log.warning("restart_agent_session: continue-marker send-keys did not land")
        return True

    def _send_keys_text(self, text: str) -> bool:
        """Send *text* to the session as a real user turn via ``tmux send-keys`` (bounded retry).

        A freshly created pane may not be ready the instant ``new-session`` returns, so
        retry up to ``send_keys_retries`` times with a small ``send_keys_delay`` between
        attempts.  Emits ``send-keys -t <session> '<text>' Enter`` — the trailing
        ``Enter`` submits it as a real user turn.  Returns ``True`` once a send lands.
        Shared by the self-heal continue-marker (:meth:`_send_marker`) and the 4b
        takeover heads-up (:meth:`_send_takeover_heads_up`) — one send-keys path.
        """
        for attempt in range(1, self.config.send_keys_retries + 1):
            rc = self._run_tmux(
                ["send-keys", "-t", self.config.session, text, "Enter"]
            )
            if rc == 0:
                return True
            if attempt < self.config.send_keys_retries:
                self._sleep(self.config.send_keys_delay)
        return False

    def _send_marker(self) -> bool:
        """Send the continue-marker to the session (self-heal restart); see :meth:`_send_keys_text`."""
        return self._send_keys_text(self.config.marker)

    def _send_takeover_heads_up(self) -> bool:
        """Send the 4b takeover heads-up (:data:`TAKEOVER_HEADS_UP`) to the CLI incumbent.

        Best-effort GRACE nudge (design §346): lands as a real acting turn if the
        incumbent is idle, or queues mid-loop — either way the incumbent is warned it is
        about to be handed off before the bounded grace elapses.  Feasible ONLY toward
        the tmux (CLI) agent (the DIRECTIONAL limit).  Delegates to the shared
        :meth:`_send_keys_text`.
        """
        return self._send_keys_text(TAKEOVER_HEADS_UP)

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

    def capture_agent_output(self) -> str | None:
        """Return the agent pane's captured MAIN-screen text (scrollback), or None.

        ``tmux capture-pane -p -S -<n> -E - -t <session>`` prints the pane's content
        through the END of history.  With ``remain-on-exit on`` the pane PERSISTS
        after the agent exits (a dead pane), so this recovers the exited agent's
        main-screen output — which the self-heal host relies on (via ``podman
        logs``) to show the user WHY the agent died.  A ``teardown`` launch does
        NOT arm ``remain-on-exit`` (the pane simply closes on exit), so this
        capture is only meaningful for the self-heal / panel modes that keep it
        armed.  Under the supervisor PID-1 that output lives in the tmux pane,
        NOT PID-1's own stdout,
        so without echoing it here ``podman logs`` would be empty and both surfaces
        would silently break (the E2c/E2d observability residual).

        ⚑ SCOPE: this recovers MAIN-screen output — an early cold-start error the
        agent prints before any TUI (e.g. claude's "No conversation found", a config
        error, an immediate crash) — which is the class the host actually acts on.
        An agent that dies while in the tmux ALTERNATE screen (a running full-screen
        TUI mid-session) leaves only tmux's "Pane is dead" overlay behind, which
        capture cannot see past; that output is not recoverable here.  This is a
        best-effort improvement over the pre-fix state (nothing surfaced at all), not
        a total transcript.

        Tolerant like every probe (PID-1 must never die on a tmux hiccup): a missing
        tmux / dead server / no session (``None`` output) resolves to ``None``.  The
        history is BOUNDED (``-S -<capture_history>`) so a chatty agent can't make PID-1
        dump an unbounded log; the host tails it anyway.
        """
        out = self._tmux_output(
            [
                "capture-pane", "-p",
                "-S", f"-{self.config.capture_history}",
                # ⚑ ``-E -`` = capture through the END OF HISTORY.  Without it the
                # end defaults to the VISIBLE screen, which for a dead pane is tmux's
                # "Pane is dead (status N)" overlay — so the agent's actual output is
                # NOT returned.  ``-E -`` reaches past that overlay into the pane's
                # scrollback where the exited agent's final output lives (verified on
                # real tmux: 20/20 instant crashes captured with ``-E -``, 0/20
                # without).
                "-E", "-",
                "-t", self.config.session,
            ]
        )
        if out is None:
            return None
        # Strip trailing blank lines tmux pads the region with, but keep the
        # meaningful body verbatim (the host greps it for target-specific sentinels).
        return out.rstrip("\n") or None

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

    def _kill_process_group(self, pid: int, sig: int) -> bool:
        """Send *sig* to *pid*'s whole PROCESS GROUP (child-kill-with-parent); tolerant.

        The agent is launched as ``tmux new-session -- <agent>`` (no shell wrap), so
        tmux starts it as a session/process-group LEADER — its PID is the group id — and
        every subagent / worker it spawns inherits that group.  Signalling the GROUP
        (``os.killpg``) therefore reaps the agent AND all its descendants, so no orphaned
        worker survives a takeover/teardown (the child-kill-with-parent ruling, design
        §267 / §348).  Resolves the real pgid via ``os.getpgid`` (falling back to the pid
        itself when it cannot — a group leader's pgid == its pid), then signals it.
        Tolerant like every PID-1 op: a process that already exited
        (``ProcessLookupError``) or any ``OSError`` resolves to ``False`` and is logged
        at debug — PID-1 never dies on a kill hiccup.

        ⚑ SAFETY GUARD (a wrong pgid here could kill the whole box): a real pane agent
        is its OWN ``setsid`` session/group leader, so its pgid is never 0/1 nor the
        SUPERVISOR's (PID-1) own group.  Refuse those pathological targets — most
        importantly ``pid <= 0``, because ``os.getpgid(0)`` returns the CALLER's group,
        so a stray ``0`` would make ``os.killpg`` signal PID-1's own group → box death.
        The guard can never block a legitimate eviction (a pane group is none of these).
        """
        if pid <= 1:
            log.warning("kill process group: refusing unsafe pid %d (would risk PID-1)", pid)
            return False
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = pid
        try:
            own_pgrp = os.getpgrp()
        except OSError:
            own_pgrp = -1
        if pgid <= 1 or pgid == own_pgrp:
            log.warning(
                "kill process group: refusing unsafe target pgid %d for pid %d "
                "(0/1/supervisor group → would risk PID-1 / the box)",
                pgid, pid,
            )
            return False
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return False
        except OSError as exc:
            log.debug("kill process group %d (pid %d) failed: %s", pgid, pid, exc)
            return False
        return True

    def kill_agent_session(self) -> None:
        """Evict the supervised agent: process-group kill each pane agent, then the session.

        The eviction primitive — used on explicit teardown (SIGTERM / :meth:`teardown`),
        on a self-heal restart (to free the dead-pane name), AND as the 4b single-writer
        EVICT (the process-group handling this method's docstring RESERVED for increment
        4).  First reaps each of the supervisor's OWN pane agents (:meth:`_own_agent_pids`)
        as a PROCESS GROUP (:meth:`_kill_process_group`), so no orphaned subagent / worker
        survives (the child-kill-with-parent ruling); then ``tmux kill-session`` removes
        the (now-dead) session so its name is reusable.  Tolerant throughout — an
        already-absent session / dead pane / missing tmux all resolve to a logged no-op,
        never an exception (PID-1 must not die on a kill).
        """
        for pid in sorted(self._own_agent_pids()):
            self._kill_process_group(pid, signal.SIGTERM)
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

    # -- agent markers: liveness (E2f) + newcomer detection (4a) -------------

    def _scan_markers(self) -> tuple[set[int], set[int]]:
        """Enumerate the markers dir → (LIVE pids, STALE pids); tolerant.

        Delegates to :func:`scan_marker_pids` with the injected lister + liveness
        probe.  No dir configured, or any unexpected raise, resolves to two EMPTY
        sets — PID-1 must never die on a marker enumeration.
        """
        markers_dir = self.config.agent_markers_dir
        if not markers_dir:
            return set(), set()
        try:
            return scan_marker_pids(
                markers_dir,
                list_pids=self._list_marker_pids,
                pid_alive=self._pid_alive,
            )
        except Exception:
            log.debug("_scan_markers: enumeration raised; treating as no markers")
            return set(), set()

    def _own_agent_pids(self) -> set[int]:
        """The PIDs of the agent(s) the supervisor itself launched in its tmux session.

        ``tmux list-panes -s -t <session> -F '#{pane_pid}'`` lists the ROOT process of
        every pane in the session — and because the agent is launched as ``new-session
        -- <agent argv>`` (no shell wrap), a pane's root process IS the agent.  So the
        pane PIDs are the supervisor's OWN agents (E2g: the marker they write via
        ``$PPID`` equals the pane PID), which the 4a newcomer detection excludes.  In
        panel-watch steady state there is no tmux agent, so this is EMPTY — the fronted
        panel agent is not a pane the supervisor launched, so the panel-watch loop adds
        that fronted incumbent to 'own' separately.  Tolerant: a missing tmux / dead
        server / no session (``None`` output) → empty set; never raises.
        """
        out = self._tmux_output(
            ["list-panes", "-s", "-t", self.config.session, "-F", "#{pane_pid}"]
        )
        if not out:
            return set()
        pids: set[int] = set()
        for tok in out.split():
            try:
                pids.add(int(tok))
            except ValueError:
                continue
        return pids

    def _log_newcomers(self, live_pids: set[int], own_pids: set[int]) -> None:
        """LOG-ONLY (increment 4a): warn once per newcomer; take NO action.

        A newcomer is a LIVE marker PID that is not one of *own_pids* (the supervisor's
        legitimate agent(s) for this mode) — a second agent has bound the session.  4a
        only LOGS: it emits ONE warning per newly-seen newcomer (deduped via
        :attr:`_reported_newcomers`, re-pinned to the current newcomer set so a
        departed-then-returning PID re-logs) and does NOTHING else — NO SIGSTOP, kill,
        evict, or send-keys.  4b will act on exactly this signal.
        """
        newcomers = newcomer_pids(live_pids, own_pids)
        for pid in sorted(newcomers - self._reported_newcomers):
            log.warning(
                "newcomer agent PID %s detected on session "
                "(increment 4a: detection only, no action)",
                pid,
            )
        self._reported_newcomers = newcomers

    # -- 4b ENFORCEMENT: single-writer takeover (grace + pause + evict) -------

    def _signal_pid(self, pid: int, sig: int) -> bool:
        """``os.kill(pid, sig)`` — a tolerant SINGLE-process signal (SIGSTOP/SIGCONT).

        Distinct from :meth:`_kill_process_group` (which signals a whole group): the
        newcomer PAUSE/RESUME target ONE process (the panel agent), never its group.
        Tolerant like every PID-1 op: a vanished process (``ProcessLookupError``) or any
        ``OSError`` resolves to ``False`` (logged at debug), never an exception.
        """
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            log.debug("session takeover: pid %d gone before signal %s", pid, sig)
            return False
        except OSError as exc:
            log.debug("session takeover: signal %s to pid %d failed: %s", sig, pid, exc)
            return False
        return True

    def _resume(self, pids: list[int]) -> None:
        """``SIGCONT`` every PID in *pids* — the reversible undo of the newcomer PAUSE.

        Called on ANY takeover error before the evict (never leave a newcomer frozen)
        AND after a successful evict (single-writer is now guaranteed).  Best-effort per
        PID (a newcomer that died while paused is simply skipped).
        """
        for pid in pids:
            self._signal_pid(pid, signal.SIGCONT)

    def _takeover(self, own_pids: set[int], newcomers: set[int]) -> bool:
        """4b ENFORCEMENT: pause the newcomer, grace the incumbent, evict it, resume (NEW-wins).

        The single-writer TAKEOVER for the CRITICAL direction (a VS Code panel newcomer
        over a live CLI incumbent).  Steps, IN ORDER — reversible ops (1-3) kept clearly
        separate from the ONE destructive op (4):

        1. PAUSE (reversible) — ``SIGSTOP`` every newcomer PID so it cannot take a
           divergent WRITE turn while the session is handed over.
        2. HEADS-UP (reversible, best-effort) — ``tmux send-keys`` a heads-up to the
           incumbent (feasible only because it is the tmux agent).
        3. GRACE (reversible) — wait ``config.takeover_grace`` seconds for the incumbent
           to wind down / checkpoint.
        4. EVICT (the ONE destructive op) — process-group kill the incumbent
           (:meth:`kill_agent_session`, child-kill-with-parent) so no orphaned subagent
           survives.  The TARGET is the pane incumbent (``own_pids`` / re-read
           :meth:`_own_agent_pids`), NEVER a bare marker PID.
        5. RESUME — ``SIGCONT`` the paused newcomer(s); single-writer is now guaranteed.

        SAFETY: on ANY error BEFORE the evict, the newcomer is ``SIGCONT``'d (never left
        frozen) and the incumbent is NOT killed → returns ``False`` (no takeover).  Also
        returns ``False`` (no eviction) when NO newcomer could be paused (every one
        vanished first) — there is then nothing to hand the session to.  Returns ``True``
        ONLY once the incumbent has been evicted, so the caller hands off to the
        agentless keep-alive.  The caller pre-checks ``own_pids`` is non-empty (there IS
        a pane incumbent to evict), so this never fires on a bare marker.
        """
        log.warning(
            "session takeover (4b): panel newcomer(s) %s over live CLI incumbent(s) %s "
            "— pausing newcomer, gracing + evicting incumbent (NEW-wins)",
            sorted(newcomers), sorted(own_pids),
        )
        paused: list[int] = []
        try:
            # 1. PAUSE the newcomer(s) BEFORE they can write (reversible).
            for pid in sorted(newcomers):
                if self._signal_pid(pid, signal.SIGSTOP):
                    paused.append(pid)
            if not paused:
                # Every newcomer vanished before it could be paused → nothing to hand
                # the session to; do NOT evict the legitimate incumbent.
                log.warning(
                    "session takeover: no newcomer could be paused; aborting (no eviction)"
                )
                return False
            # 2. HEADS-UP to the incumbent (best-effort) + 3. GRACE (reversible).
            self._send_takeover_heads_up()
            self._sleep(self.config.takeover_grace)
        except Exception:
            log.exception(
                "session takeover: a pre-evict step failed; "
                "resuming newcomer(s), NOT evicting the incumbent"
            )
            self._resume(paused)
            return False
        # 4. EVICT (the single DESTRUCTIVE op): process-group kill the pane incumbent.
        log.warning(
            "session takeover: grace elapsed; evicting incumbent process group(s) %s",
            sorted(own_pids),
        )
        try:
            self.kill_agent_session()
        finally:
            # 5. RESUME the newcomer — single-writer is now guaranteed (incumbent gone).
            self._resume(paused)
        return True

    def panel_agent_state(self) -> PanelAgentState:
        """Liveness of the PANEL-launched agent from the per-PID markers dir (E2f).

        Enumerates the markers dir (:meth:`_scan_markers`) and EXCLUDES the supervisor's
        OWN tmux-pane agents (:meth:`_own_agent_pids`) — a self-healed CLI agent writes
        a marker too and it is NOT the panel.  TOLERANT throughout — PID-1 must never
        die on a bad marker:

        * no markers dir configured, or NO non-own markers at all (never started, or a
          clean SessionEnd removed each) → :data:`PanelAgentState.NONE`;
        * ≥1 LIVE non-own marker (a panel agent's live PID) → :data:`PanelAgentState.ALIVE`;
        * no live non-own marker but ≥1 STALE non-own marker (a crash left a per-PID
          file behind) → :data:`PanelAgentState.DEAD`.

        This preserves the E2f contract the single-pidfile scheme drove (a clean exit →
        NONE, a crash → DEAD → SELF_HEAL_CLI), now over the per-PID dir.

        ⚑ AGENT ASYMMETRY (Phase 2 D2): only claude has a marker-REMOVE hook —
        codex's hook surface has NO SessionEnd/exit event (verified against
        codex-rs at rust-v0.141.0 and 0.144.x), so a cleanly-exited codex agent
        leaves a stale marker and reads DEAD here, never NONE.  Intended: the
        codex panel's sessions are threads inside one long-lived ``codex
        app-server`` process, so its marker-PID dying ≈ the panel process
        itself is gone — DEAD (→ SELF_HEAL_CLI when the VS Code server is still
        attached) is the useful verdict, and at worst a clean exit costs one
        benign self-heal.  Do NOT "fix" this by janitor-unlinking stale
        markers at scan: DEAD-vs-NONE is exactly the information
        :func:`decide_panel` consumes.
        """
        if not self.config.agent_markers_dir:
            return PanelAgentState.NONE
        own = self._own_agent_pids()
        live, stale = self._scan_markers()
        if live - own:
            return PanelAgentState.ALIVE
        if stale - own:
            return PanelAgentState.DEAD
        return PanelAgentState.NONE

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
        mount), and a TRUSTED HOST watcher (:mod:`kanibako.launch.creds_watcher`) does the
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
          surface, close the box with a TRUTHFUL-as-possible exit code.  A teardown
          launch does NOT arm ``remain-on-exit`` (:meth:`_arm_and_start_session`), so
          on exit the pane simply CLOSES and there is no ``#{pane_dead_status}`` to
          read.  So the code is derived as: an agent that CAME UP and then exited
          (``started``) returns **0** — a clean quit must NOT masquerade as a failure
          (the whole point of dropping the dead pane), and a sole-agent CRASH is
          indistinguishable from a clean exit here so it ALSO returns 0 until a
          pipe-pane follow-up restores truthful crash codes; an agent that NEVER came
          up (the initial start failed → ``not started``) returns **1**, a real
          failure the host must surface.  (If a ``#{pane_dead_status}`` IS somehow
          present — e.g. a stale armed pane — it is honored verbatim.)
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
        # Did the agent ever COME UP?  A warm box already has a live session; a cold
        # box is up iff the initial start returns rc 0.  This distinguishes, in the
        # teardown branch below, a NEVER-STARTED failure (start failed → the box
        # closes rc 1) from a normal ran-then-exited quit (→ rc 0) once the pane has
        # closed and there is no dead-pane status to read.  Defaults True so a
        # startup-probe hiccup (the except path) prefers 0 (never fabricate a
        # failure from a clean quit).
        started = True
        try:
            if not self.agent_session_alive():
                log.info("no live agent session at startup; starting one detached")
                started = self.start_agent_session()
                if not started:
                    # A failed INITIAL start is handled UNIFORMLY by the loop's first
                    # tick (E2e factoring): under 'self-heal' the loop self-heals a
                    # missing agent; under 'teardown' the loop's SURFACE-AWARE branch
                    # closes the box (rc 1 — never came up) UNLESS a panel is attached,
                    # in which case it stays up as an agentless keep-alive.  No special-
                    # case return here (one start attempt, then the loop's ref-count
                    # policy decides) keeps the teardown decision in exactly ONE place
                    # (design §86-88, cold-start-error-human-direct).
                    log.warning("initial agent start failed; deferring to the loop policy")
            prev = self._snapshot()
        except Exception:
            log.exception("supervisor startup probe raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                # PID-1 duty FIRST: reap exited (orphan-reparented) children so
                # a dead panel process cannot sit as a zombie and read ALIVE to
                # the marker probes below.
                reap_zombie_children()
                cur = self._snapshot()
                alive = self.agent_session_alive()
                # Newcomer detection: a live marker PID that is NOT this supervisor's
                # own tmux agent = a second agent bound the session (the split-brain
                # hazard — e.g. the VS Code panel auto-`--resume` over a live CLI agent).
                # Gated on a configured markers dir so an old launcher (or a test) that
                # threads none is byte-unchanged.
                if self.config.agent_markers_dir:
                    live_markers, _stale = self._scan_markers()
                    own = self._own_agent_pids()
                    if self.config.session_takeover:
                        # 4b ENFORCEMENT (single-writer takeover, NEW-wins).  The
                        # CRITICAL direction: a panel newcomer over a LIVE CLI incumbent.
                        # Act ONLY when there IS a pane incumbent to evict (own non-empty)
                        # — never evict on a bare marker PID.  On success the incumbent is
                        # gone and the newcomer is the sole writer, so hand off to the
                        # agentless panel-watch keep-alive (E2f self-heal-to-CLI on the
                        # newcomer's death).
                        newcomers = newcomer_pids(live_markers, own)
                        if newcomers and own and self._takeover(own, newcomers):
                            return self._run_panel_watch()
                    else:
                        # Flag OFF (default): 4a LOG-ONLY — no signals, no kills.
                        self._log_newcomers(live_markers, own)
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
                            # No other surface → close the box (E2e teardown).  A
                            # teardown launch does NOT arm ``remain-on-exit``, so the
                            # pane has CLOSED and ``#{pane_dead_status}`` is normally
                            # absent.  Derive a truthful-as-possible code:
                            #   * a dead pane status IS present (stale/armed) ⇒ honor it;
                            #   * else the agent CAME UP and exited (``started``) ⇒ 0 —
                            #     a clean quit must not masquerade as a failure (a
                            #     sole-agent CRASH also lands here as 0 until a pipe-pane
                            #     follow-up restores truthful crash codes);
                            #   * else it NEVER came up (initial start failed) ⇒ 1.
                            status = self.agent_pane_dead_status()
                            if status is not None:
                                code = status
                            else:
                                code = 0 if started else 1
                            # Surface any captured agent output to the HOST before
                            # closing.  The host's foreground path reads ``podman logs``
                            # (PID-1's stdout) to show WHY the agent died — but the agent
                            # ran in a tmux pane, so its output never reached PID-1's
                            # stdout.  Echo the captured pane here (best-effort).  With
                            # ``remain-on-exit`` OFF in teardown the pane has closed and
                            # this normally captures nothing; it still recovers output
                            # from a stale/armed pane if one is present.
                            captured = self.capture_agent_output()
                            if captured:
                                print(captured, flush=True)
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
        # 4a: the fronted panel incumbent PID (first live non-own marker seen).  A
        # panel-watch box's LEGITIMATE agent is the fronted panel (a non-own marker) or
        # a self-healed CLI (an own tmux pane); a SECOND live non-own marker beyond the
        # latched incumbent is a newcomer (a concurrent panel resume).  Latched here so
        # the fronted panel is not itself flagged.  Cleared when it departs the live set.
        panel_incumbent: int | None = None
        try:
            prev = self._snapshot()
            if prev.any_attached:
                seen_surface = True
        except Exception:
            log.exception("panel-watch startup snapshot raised; entering loop defensively")
            prev = AttachState()

        while not self._stop:
            try:
                # PID-1 duty FIRST: reap exited (orphan-reparented) children so
                # a dead panel agent cannot sit as a zombie and hold
                # panel_agent_state at ALIVE forever (wedging SELF_HEAL_CLI and
                # TEARDOWN alike).
                reap_zombie_children()
                cur = self._snapshot()
                if cur.any_attached:
                    seen_surface = True
                if classify_transition(prev, cur) is LifecycleEvent.DETACH:
                    self._safe_on_detach()
                tmux_alive = self.agent_session_alive()
                panel = self.panel_agent_state()
                # 4a LOG-ONLY newcomer detection (panel-watch): own = the self-healed
                # CLI's tmux pane(s) PLUS the latched fronted panel incumbent; a live
                # marker outside that set is a newcomer.  NO action (4b acts).
                if self.config.agent_markers_dir:
                    own = self._own_agent_pids()
                    live_markers, _stale = self._scan_markers()
                    non_own_live = live_markers - own
                    if panel_incumbent is not None and panel_incumbent not in non_own_live:
                        panel_incumbent = None
                    if panel_incumbent is None and non_own_live:
                        panel_incumbent = min(non_own_live)
                    incumbent = (
                        {panel_incumbent} if panel_incumbent is not None else set()
                    )
                    self._log_newcomers(live_markers, own | incumbent)
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
        "--session-takeover",
        action="store_true",
        help=(
            "4b ENFORCEMENT (default OFF): enable single-writer TAKEOVER — a panel "
            "newcomer over a live CLI incumbent is paused, the incumbent graced + "
            "process-group evicted, then the newcomer resumed.  Omit for 4a LOG-ONLY "
            "(no signals/kills).  Threaded from the experimental KANIBAKO_SESSION_TAKEOVER"
        ),
    )
    parser.add_argument(
        "--takeover-grace",
        type=float,
        default=5.0,
        help="seconds to grace the CLI incumbent before the 4b process-group evict",
    )
    parser.add_argument(
        "--panel-watch",
        action="store_true",
        help=(
            "PANEL-WATCH mode (E2f): start NO CLI agent; enumerate the per-agent markers "
            "dir (--agent-markers-dir) + the VS Code server surface and self-heal a CLI "
            "agent only when the panel agent dies with the panel still connected"
        ),
    )
    parser.add_argument(
        "--agent-markers-dir",
        default=None,
        help=(
            "box-local per-agent liveness MARKERS dir (each agent writes <dir>/$PPID): "
            "enumerated for panel-agent liveness (--panel-watch) and LOG-ONLY newcomer "
            "detection (increment 4a) in both modes"
        ),
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
        session_takeover=ns.session_takeover,
        takeover_grace=ns.takeover_grace,
        panel_watch=ns.panel_watch,
        agent_markers_dir=ns.agent_markers_dir,
        creds_flag=ns.creds_flag,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: parse args, build the supervisor, run the watch loop forever.

    ``python3 -m kanibako.box_supervisor --session NAME --marker 'STR' [--poll SEC]
    [--max-retries N] [--continue-cmd 'ARGV'] [--on-agent-exit self-heal|teardown]
    [--session-takeover] [--takeover-grace SEC] [--panel-watch]
    [--agent-markers-dir DIR] [--creds-flag PATH] -- <agent entrypoint + argv...>``

    In ``--panel-watch`` mode (E2f) the trailing ``-- <agent argv>`` is OMITTED (no
    agent starts at launch); ``--continue-cmd`` carries the self-heal grammar.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config = config_from_argv(args)
    # Strip the host-package mount from PYTHONPATH before the loop spawns tmux / the
    # agent, so those children do not inherit our import path (see
    # :func:`scrub_bootstrap_pythonpath`).  Safe here: this process's own imports are
    # already resolved, and PYTHONPATH only affects newly spawned processes.
    scrub_bootstrap_pythonpath()
    supervisor = BoxSupervisor(config)
    return supervisor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
