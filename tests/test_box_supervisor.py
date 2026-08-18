"""Tests for kanibako.box_supervisor — the PID-1 always-on-agent supervisor.

Increment 2 (E2a) is the supervisor MODULE only.  The PURE decision function
(:func:`decide`) is exercised exhaustively; the impure tmux actions are driven
through an injected fake ``run`` (asserting the exact tmux argv), and the loop /
self-heal / backoff run instantly via an injected ``sleep`` — no real tmux, no
real agent, no real waiting.
"""

from __future__ import annotations

import hashlib as _hashlib
import importlib.resources as _resources
import json as _json
import os as _os
import signal
import subprocess
import sys as _sys
import time as _time
from pathlib import Path as _Path

import pytest

from kanibako.box_lifecycle import AttachState
from kanibako import box_supervisor as bs
from kanibako.box_supervisor import (
    DIRECTIVE_MANIFEST_VERSION,
    ActionKind,
    BoxSupervisor,
    DirectiveVerdict,
    DirectiveWatch,
    PanelActionKind,
    PanelAgentState,
    SupervisorConfig,
    _default_list_marker_pids,
    config_from_argv,
    decide,
    decide_directives,
    decide_panel,
    main,
    newcomer_pids,
    scan_marker_pids,
)

# --- attach-state fixtures (mirror box_lifecycle's surfaces) ----------------

_NONE = AttachState(vscode_server=False, tmux_terminal=False)
_TM = AttachState(vscode_server=False, tmux_terminal=True)
_VS = AttachState(vscode_server=True, tmux_terminal=False)
_BOTH = AttachState(vscode_server=True, tmux_terminal=True)

_MARKER = "[Agent handoff - Continue prior task(s)]"

# The agent start/restart arms ``remain-on-exit`` in ONE tmux invocation that:
# global-arms BEFORE new-session (to win the instant-crash race), starts the
# detached session, pins the option SESSION-LOCAL, then REVERTS the global (so
# sibling windows in the box's tmux server don't accumulate dead panes).  These are
# the exact combined argvs the supervisor emits.
_START_CALL = [
    "tmux", "set-option", "-g", "remain-on-exit", "on", ";",
    "new-session", "-d", "-s", "kanibako", "--",
    "claude", "--dangerously-skip-permissions", ";",
    "set-option", "-t", "kanibako", "remain-on-exit", "on", ";",
    "set-option", "-g", "remain-on-exit", "off",
]
_CONTINUE_CALL = [
    "tmux", "set-option", "-g", "remain-on-exit", "on", ";",
    "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue", ";",
    "set-option", "-t", "kanibako", "remain-on-exit", "on", ";",
    "set-option", "-g", "remain-on-exit", "off",
]


def _config(**over: object) -> SupervisorConfig:
    base: dict[str, object] = dict(
        session="kanibako",
        start_argv=["claude", "--dangerously-skip-permissions"],
        continue_argv=["claude", "--continue"],
        marker=_MARKER,
        poll_interval=2.0,
        max_restart_retries=3,
        backoff_base=0.5,
        send_keys_retries=3,
        send_keys_delay=0.1,
    )
    base.update(over)
    return SupervisorConfig(**base)  # type: ignore[arg-type]


class FakeRun:
    """A ``subprocess.run`` stand-in that dispatches on the tmux subcommand.

    Records every argv; returns a ``CompletedProcess`` whose rc / stdout come from
    per-subcommand programmes.  A programme value may be a plain value (used for
    every call) or a list (consumed one entry per call, last value sticking) so a
    test can script "dead, then alive across ticks".  ``raise_on`` names a
    subcommand that should raise ``FileNotFoundError`` (simulating a missing tmux).
    """

    def __init__(
        self,
        *,
        rc: dict[str, object] | None = None,
        stdout: dict[str, object] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._rc = rc or {}
        self._stdout = stdout or {}
        self._raise_on = raise_on
        self.calls: list[list[str]] = []

    @staticmethod
    def _take(prog: object) -> object:
        if isinstance(prog, list):
            if not prog:
                return None
            return prog[0] if len(prog) == 1 else prog.pop(0)
        return prog

    @staticmethod
    def _match(args, table: dict[str, object]) -> str | None:
        """Return the first arg token that keys into *table*.

        A single tmux invocation can now carry MORE THAN ONE subcommand (the agent
        start arms ``remain-on-exit`` globally in the same call: ``set-option -g … ;
        new-session …``), so dispatch/matching scans every token rather than only
        ``args[1]`` — a combined call is matched by either ``set-option`` or
        ``new-session``.  The subcommand tokens are distinct, so there is no
        cross-match.
        """
        for tok in args[1:]:
            if tok in table:
                return tok
        return None

    def __call__(self, args, **kwargs):
        assert args and args[0] == "tmux"
        self.calls.append(list(args))
        if self._raise_on is not None and self._raise_on in args[1:]:
            raise FileNotFoundError("tmux not found")
        rc_key = self._match(args, self._rc)
        out_key = self._match(args, self._stdout)
        rc = self._take(self._rc[rc_key]) if rc_key is not None else 0
        out = self._take(self._stdout[out_key]) if out_key is not None else ""
        return subprocess.CompletedProcess(args, returncode=int(rc), stdout=str(out), stderr="")

    def sub_calls(self, sub: str) -> list[list[str]]:
        return [c for c in self.calls if sub in c[1:]]


# ---------------------------------------------------------------------------
# decide (the pure heart).
# ---------------------------------------------------------------------------

def test_decide_alive_no_change_is_none_no_hook():
    action = decide(_TM, _TM, agent_alive=True)
    assert action.kind is ActionKind.NONE
    assert action.fire_detach_hook is False


def test_decide_agent_died_is_self_heal():
    action = decide(_TM, _TM, agent_alive=False)
    assert action.kind is ActionKind.SELF_HEAL
    assert action.fire_detach_hook is False


def test_decide_detach_fires_hook():
    action = decide(_TM, _NONE, agent_alive=True)
    assert action.kind is ActionKind.NONE
    assert action.fire_detach_hook is True


def test_decide_detach_of_one_surface_while_other_stays():
    # BOTH -> VS: the tmux terminal surface was lost => DETACH (box_lifecycle bias).
    action = decide(_BOTH, _VS, agent_alive=True)
    assert action.fire_detach_hook is True


def test_decide_died_and_detach_same_tick_is_both():
    action = decide(_TM, _NONE, agent_alive=False)
    assert action.kind is ActionKind.SELF_HEAL
    assert action.fire_detach_hook is True


@pytest.mark.parametrize("prev,cur", [(_NONE, _TM), (_NONE, _VS), (_VS, _BOTH)])
def test_decide_attach_transition_no_detach_hook(prev, cur):
    action = decide(prev, cur, agent_alive=True)
    assert action.fire_detach_hook is False


def test_decide_none_transition_no_detach_hook():
    action = decide(_NONE, _NONE, agent_alive=True)
    assert action.fire_detach_hook is False


# ---------------------------------------------------------------------------
# tmux action helpers — exact argv.
# ---------------------------------------------------------------------------

def test_start_agent_session_emits_new_session_detached():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is True
    # ONE invocation arms remain-on-exit globally then starts the detached session.
    assert fake.sub_calls("new-session") == [_START_CALL]


def test_start_agent_session_teardown_does_not_arm_remain_on_exit():
    # SOLE / FOREGROUND launch (on_agent_exit="teardown"): NO remain-on-exit is
    # armed, so the pane closes on ANY exit (clean or crash) and an attached
    # foreground client returns to the shell instead of stranding on a dead pane.
    fake = FakeRun()
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    assert sup.start_agent_session() is True
    # A PLAIN detached new-session — no combined set-option arm around it.
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--",
         "claude", "--dangerously-skip-permissions"]
    ]
    # And NO remain-on-exit was set at all (neither global nor session-local).
    assert fake.sub_calls("set-option") == []
    assert not any("remain-on-exit" in c for c in fake.calls)


def test_restart_agent_session_teardown_does_not_arm_remain_on_exit():
    # A teardown launch never self-heals, but the arming path is shared, so a
    # (defensive) restart under teardown must ALSO stay plain — no dead pane.
    fake = FakeRun()
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    assert sup.restart_agent_session() is True
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue"]
    ]
    assert not any("remain-on-exit" in c for c in fake.calls)


def test_start_agent_session_reports_failure_on_nonzero_rc():
    fake = FakeRun(rc={"new-session": 1})
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is False


def test_start_agent_session_semicolon_in_argv_falls_back_to_plain_form():
    # A standalone ';' token in the agent argv would be mis-read by tmux as a
    # command separator in the COMBINED arm+start invocation, breaking the launch.
    # Guard: fall back to a plain new-session (';' is safe as a plain agent arg) then
    # a best-effort per-session arm — launch stays correct.
    fake = FakeRun()
    sup = BoxSupervisor(
        _config(start_argv=["claude", ";", "--model", "opus"]), run=fake
    )
    assert sup.start_agent_session() is True
    # NOT the combined form (no leading set-option -g in the new-session call).
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--",
         "claude", ";", "--model", "opus"]
    ]
    # arm is a SEPARATE per-session set-option (best-effort) after the start.
    assert fake.sub_calls("set-option") == [
        ["tmux", "set-option", "-t", "kanibako", "remain-on-exit", "on"]
    ]


def test_restart_agent_session_uses_continue_argv_and_sends_marker():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.restart_agent_session() is True
    assert fake.sub_calls("new-session") == [_CONTINUE_CALL]
    assert fake.sub_calls("send-keys") == [
        ["tmux", "send-keys", "-t", "kanibako", _MARKER, "Enter"]
    ]


def test_restart_agent_session_fails_when_new_session_fails_no_send_keys():
    fake = FakeRun(rc={"new-session": 1})
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.restart_agent_session() is False
    assert fake.sub_calls("send-keys") == []


def test_send_marker_retries_until_it_lands():
    # send-keys fails twice then succeeds; sleep between the failed attempts.
    fake = FakeRun(rc={"send-keys": [1, 1, 0]})
    slept: list[float] = []
    sup = BoxSupervisor(_config(), run=fake, sleep=slept.append)
    # drive send-keys directly via a restart (new-session rc 0)
    assert sup.restart_agent_session() is True
    assert len(fake.sub_calls("send-keys")) == 3
    assert slept == [0.1, 0.1]


def test_agent_session_alive_true_on_rc0_false_on_nonzero():
    alive = BoxSupervisor(_config(), run=FakeRun(rc={"has-session": 0}))
    assert alive.agent_session_alive() is True
    dead = BoxSupervisor(_config(), run=FakeRun(rc={"has-session": 1}))
    assert dead.agent_session_alive() is False
    assert dead._run.sub_calls("has-session") == [  # type: ignore[attr-defined]
        ["tmux", "has-session", "-t", "kanibako"]
    ]


def test_capture_agent_output_reads_full_history_and_strips_padding():
    # capture-pane must reach the END OF HISTORY (-E -) — for a dead pane the
    # visible screen is tmux's "Pane is dead" overlay, so the agent's real output
    # only lives in scrollback.
    fake = FakeRun(stdout={"capture-pane": "No conversation found\n\n\n"})
    sup = BoxSupervisor(_config(capture_history=200), run=fake)
    assert sup.capture_agent_output() == "No conversation found"
    assert fake.sub_calls("capture-pane") == [
        ["tmux", "capture-pane", "-p", "-S", "-200", "-E", "-", "-t", "kanibako"]
    ]


def test_capture_agent_output_tolerant_and_empty_to_none():
    # non-zero rc / missing tmux -> None (tolerant, never raises).
    assert BoxSupervisor(_config(), run=FakeRun(rc={"capture-pane": 1})).capture_agent_output() is None
    assert BoxSupervisor(_config(), run=FakeRun(raise_on="capture-pane")).capture_agent_output() is None
    # an all-blank pane -> None (nothing meaningful to surface).
    assert BoxSupervisor(_config(), run=FakeRun(stdout={"capture-pane": "\n\n"})).capture_agent_output() is None


def test_agent_pane_dead_status_parses_int_none_and_is_tolerant():
    # dead pane: display-message prints the integer exit code.
    dead = BoxSupervisor(_config(), run=FakeRun(stdout={"display-message": "37\n"}))
    assert dead.agent_pane_dead_status() == 37
    assert dead._run.sub_calls("display-message") == [  # type: ignore[attr-defined]
        ["tmux", "display-message", "-p", "-t", "kanibako", "#{pane_dead_status}"]
    ]
    # live pane: empty output -> None (not dead).
    live = BoxSupervisor(_config(), run=FakeRun(stdout={"display-message": ""}))
    assert live.agent_pane_dead_status() is None
    # non-zero rc (no session) -> None.
    gone = BoxSupervisor(_config(), run=FakeRun(rc={"display-message": 1}))
    assert gone.agent_pane_dead_status() is None
    # missing tmux (raises) -> None (tolerant, never propagates).
    notmux = BoxSupervisor(_config(), run=FakeRun(raise_on="display-message"))
    assert notmux.agent_pane_dead_status() is None
    # unparseable output -> None.
    junk = BoxSupervisor(_config(), run=FakeRun(stdout={"display-message": "notanint"}))
    assert junk.agent_pane_dead_status() is None


def test_agent_session_alive_false_on_dead_pane_true_when_live():
    # has-session rc 0 but the pane is DEAD (remain-on-exit) => NOT alive.
    dead = BoxSupervisor(
        _config(), run=FakeRun(rc={"has-session": 0}, stdout={"display-message": "1"})
    )
    assert dead.agent_session_alive() is False
    # has-session rc 0 AND no dead pane (empty status) => alive.
    live = BoxSupervisor(
        _config(), run=FakeRun(rc={"has-session": 0}, stdout={"display-message": ""})
    )
    assert live.agent_session_alive() is True
    # session gone entirely (has-session != 0) => not alive, and the pane probe is
    # short-circuited (no display-message needed).
    gone = BoxSupervisor(_config(), run=FakeRun(rc={"has-session": 1}))
    assert gone.agent_session_alive() is False
    assert gone._run.sub_calls("display-message") == []  # type: ignore[attr-defined]


def test_start_agent_session_arms_remain_on_exit_globally_before_new_session():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is True
    # remain-on-exit is armed GLOBALLY in the SAME invocation as new-session, and
    # BEFORE it (the ``;`` command separator precedes ``new-session``), so the
    # option is active when the agent pane is born — an instant crash still leaves a
    # capturable dead pane.  It is the SAME call new-session lives in.
    call = fake.sub_calls("set-option")
    assert call == [_START_CALL]
    i_opt = call[0].index("set-option")
    i_new = call[0].index("new-session")
    assert i_opt < i_new  # arm BEFORE start


def test_start_agent_session_arms_remain_on_exit_in_a_single_invocation():
    # The arm and the start MUST share one tmux process (a separate ``set-option
    # -g`` on a not-yet-running server starts a server that immediately exits, so the
    # global would be lost).  There is therefore exactly ONE tmux call on a clean
    # start, carrying both subcommands.
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is True
    assert fake.calls == [_START_CALL]


def test_restart_agent_session_kills_dead_session_then_arms_remain():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.restart_agent_session() is True
    # kill-session PRECEDES the fresh start call (which arms remain-on-exit globally
    # then new-sessions in one invocation) so the dead-pane name is reusable.
    kinds = [
        "kill-session" if c[1] == "kill-session" else "start"
        for c in fake.calls
        if c[1] == "kill-session" or "new-session" in c
    ]
    assert kinds == ["kill-session", "start"]
    assert fake.sub_calls("new-session") == [_CONTINUE_CALL]


def test_kill_agent_session_emits_kill_session():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    sup.kill_agent_session()
    assert fake.sub_calls("kill-session") == [["tmux", "kill-session", "-t", "kanibako"]]


# ---------------------------------------------------------------------------
# self-heal retry / backoff.
# ---------------------------------------------------------------------------

def test_self_heal_exhausts_retries_when_agent_stays_dead():
    # has-session always reports dead => retries to the max, then gives up.
    fake = FakeRun(rc={"has-session": 1})
    slept: list[float] = []
    sup = BoxSupervisor(_config(max_restart_retries=3), run=fake, sleep=slept.append)
    assert sup._self_heal() is False
    assert len(fake.sub_calls("new-session")) == 3  # one restart per attempt
    assert slept == [0.5, 1.0]  # backoff between attempts 1->2 and 2->3


def test_self_heal_stops_retrying_on_recovery():
    # dead after attempt 1, alive after attempt 2.
    fake = FakeRun(rc={"has-session": [1, 0]})
    slept: list[float] = []
    sup = BoxSupervisor(_config(max_restart_retries=3), run=fake, sleep=slept.append)
    assert sup._self_heal() is True
    assert len(fake.sub_calls("new-session")) == 2
    assert slept == [0.5]  # only one backoff before the successful second attempt


# ---------------------------------------------------------------------------
# detach hook contract.
# ---------------------------------------------------------------------------

def test_on_detach_noop_when_no_creds_flag_configured():
    # No --creds-flag threaded (old launcher): _on_detach signals nothing, never raises.
    sup = BoxSupervisor(_config(), run=FakeRun())
    assert sup._on_detach() is None
    sup._safe_on_detach()  # no exception


def test_on_detach_writes_the_creds_dirty_flag(tmp_path):
    # D Part 1: with a creds-flag configured, a detach WRITES the flag (mkdir -p'ing
    # its parent) so the trusted host watcher can act on the signal.
    flag = tmp_path / "boxhome" / ".kanibako" / "creds-dirty"
    sup = BoxSupervisor(_config(creds_flag=str(flag)), run=FakeRun())
    assert not flag.exists()
    sup._on_detach()
    assert flag.is_file()
    assert flag.read_text()  # non-empty edge-trigger marker


def test_on_detach_is_idempotent(tmp_path):
    # Edge-trigger: firing twice leaves the flag set (idempotent), never raises.
    flag = tmp_path / ".kanibako" / "creds-dirty"
    sup = BoxSupervisor(_config(creds_flag=str(flag)), run=FakeRun())
    sup._on_detach()
    sup._on_detach()
    assert flag.is_file()


def test_on_detach_tolerates_an_unwritable_flag_path(tmp_path):
    # PID-1 must never die on a flag-write error: an unwritable path is swallowed.
    # Point the flag at a path whose "parent" is a FILE, so mkdir/write fails.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    flag = blocker / "creds-dirty"
    sup = BoxSupervisor(_config(creds_flag=str(flag)), run=FakeRun())
    sup._on_detach()  # must not raise
    sup._safe_on_detach()  # must not raise
    assert not flag.exists()


def test_run_forever_detach_writes_the_real_flag(tmp_path):
    # End-to-end through the loop: a real DETACH transition fires the REAL _on_detach
    # (not a monkeypatched hook), which writes the flag — proving the wiring.
    flag = tmp_path / ".kanibako" / "creds-dirty"
    fake = FakeRun(
        rc={"has-session": 0},
        # prev-snapshot=attached, tick1=attached, tick2=detached => a DETACH on tick2.
        stdout={"list-clients": ["/dev/pts/1: ...\n", "/dev/pts/1: ...\n", ""]},
    )
    sup = BoxSupervisor(_config(creds_flag=str(flag)), run=fake, proc_cmdlines=[])
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert flag.is_file()  # the real _on_detach wrote the signal


def test_safe_on_detach_swallows_a_raising_hook():
    sup = BoxSupervisor(_config(), run=FakeRun())

    def boom() -> None:
        raise RuntimeError("cred writeback blew up")

    sup._on_detach = boom  # type: ignore[method-assign]
    sup._safe_on_detach()  # must not propagate


# ---------------------------------------------------------------------------
# teardown / SIGTERM.
# ---------------------------------------------------------------------------

def test_teardown_kills_session_and_signals_loop_exit():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup._stop is False
    sup.teardown()
    assert sup._stop is True
    assert fake.sub_calls("kill-session") == [["tmux", "kill-session", "-t", "kanibako"]]


def test_sigterm_handler_triggers_teardown():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    sup._handle_sigterm(signal.SIGTERM, None)
    assert sup._stop is True
    assert fake.sub_calls("kill-session")


def test_install_signal_handlers_tolerates_non_main_thread(monkeypatch):
    sup = BoxSupervisor(_config(), run=FakeRun())

    def raise_value_error(*_a, **_k):
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", raise_value_error)
    sup.install_signal_handlers()  # must not raise


# ---------------------------------------------------------------------------
# run_forever integration (injected sleep stops the loop; no real waiting).
# ---------------------------------------------------------------------------

def _stop_after(sup: BoxSupervisor, n: int) -> list[float]:
    """Wire the supervisor's sleep to stop the loop after *n* calls; return the log."""
    calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= n:
            sup._stop = True

    sup._sleep = fake_sleep
    return calls


def test_run_forever_starts_agent_when_absent_then_loops():
    # Agent alive throughout (has-session rc 0); no client surfaces => no detach hook.
    fake = FakeRun(rc={"has-session": [1, 0, 0]})  # absent at startup, then alive
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 1)
    fired: list[int] = []
    sup._on_detach = lambda: fired.append(1)  # type: ignore[method-assign]
    assert sup.run_forever() == 0
    # startup saw no session -> started one
    assert len(fake.sub_calls("new-session")) == 1
    assert fired == []


def test_run_forever_fires_detach_hook_on_terminal_loss():
    # Agent alive; tmux terminal attached on the prev-snapshot + tick1, gone on tick2.
    fake = FakeRun(
        rc={"has-session": 0},
        # list-clients: prev-snapshot=attached, tick1=attached, tick2=detached
        stdout={"list-clients": ["/dev/pts/1: ...\n", "/dev/pts/1: ...\n", ""]},
    )
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 2)
    fired: list[int] = []
    sup._on_detach = lambda: fired.append(1)  # type: ignore[method-assign]
    assert sup.run_forever() == 0
    assert fired == [1]  # exactly one detach hook when the terminal surface dropped


def test_run_forever_self_heals_a_dead_agent_and_continues():
    # has-session: startup alive; tick1 the agent is dead then recovers on restart.
    # sequence of has-session calls:
    #   startup check -> 0 (alive, no initial start)
    #   tick1 liveness -> 1 (dead) => self-heal; _self_heal restart then has-session -> 0
    #   tick2 liveness -> 0 (alive)
    fake = FakeRun(rc={"has-session": [0, 1, 0, 0]})
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    # one self-heal restart happened (continue grammar)
    assert fake.sub_calls("new-session") == [_CONTINUE_CALL]


def test_run_forever_exits_when_self_heal_exhausts():
    # agent alive at startup, dead every tick after, restart never takes.
    fake = FakeRun(rc={"has-session": [0, 1, 1, 1, 1, 1]})
    sup = BoxSupervisor(_config(max_restart_retries=2), run=fake, sleep=lambda _s: None)
    sup._proc_cmdlines = []
    # loop should return on its own via self-heal exhaustion (no _stop needed)
    assert sup.run_forever() == 0
    assert len(fake.sub_calls("new-session")) == 2  # max_restart_retries restarts


def test_run_forever_tick_tolerates_a_raising_run(monkeypatch):
    # A run that raises FileNotFoundError on has-session must not propagate out of the
    # loop; the tick is guarded and the loop continues to the next sleep (which stops).
    fake = FakeRun(raise_on="has-session")
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 1)
    # startup call to agent_session_alive() also hits has-session (tolerant -> False),
    # so a start is attempted; the tick then raises inside snapshot/liveness and is
    # swallowed. The loop must return cleanly.
    assert sup.run_forever() == 0


class _RaiseListClientsAfter:
    """A ``run`` that raises a NON-OSError (RuntimeError) on ``list-clients`` after
    the first *skip* calls, and behaves normally otherwise.

    ``box_lifecycle._tmux_clients_output`` only catches ``FileNotFoundError`` /
    ``OSError``, so a ``RuntimeError`` from ``list-clients`` PROPAGATES up through
    ``snapshot_attach_state`` — exactly the "snapshot raising unexpectedly" case that
    must never kill PID-1.  ``skip=1`` lets the pre-loop ``prev`` snapshot succeed so
    the raise lands INSIDE a loop tick (proving the tick guard, not the startup guard).
    """

    def __init__(self, *, skip: int = 0) -> None:
        self._skip = skip
        self._seen = 0
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        sub = args[1] if len(args) > 1 else ""
        if sub == "list-clients":
            self._seen += 1
            if self._seen > self._skip:
                raise RuntimeError("boom from snapshot probe")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")


def test_run_forever_tick_survives_a_non_tmux_snapshot_exception():
    # A RuntimeError from the snapshot path is NOT swallowed by box_lifecycle; it must
    # be contained by the LOOP's per-tick guard, not escape and kill PID-1.  skip=1 so
    # the pre-loop prev snapshot succeeds and the raise happens inside a tick.
    run = _RaiseListClientsAfter(skip=1)
    sup = BoxSupervisor(_config(), run=run, proc_cmdlines=[])
    _stop_after(sup, 2)  # need >=1 tick to survive before stopping
    assert sup.run_forever() == 0  # loop contained the raise and exited cleanly


def test_run_forever_startup_snapshot_exception_does_not_kill_pid1():
    # The PRE-LOOP prev snapshot runs before the per-tick guard; a raise there must
    # ALSO be contained (degrade to no-attach and enter the loop), not kill PID-1.
    run = _RaiseListClientsAfter(skip=0)  # every list-clients raises, incl. the first
    sup = BoxSupervisor(_config(), run=run, proc_cmdlines=[])
    _stop_after(sup, 1)
    assert sup.run_forever() == 0


def test_self_heal_budget_resets_across_separate_deaths():
    # Two INDEPENDENT deaths on the same instance: each _self_heal call must get a
    # FRESH retry budget (the counter is per-call, not persisted) — a transient death
    # must not leave a permanently-exhausted counter that starves a later self-heal.
    # Each death recovers on the 2nd attempt (has-session: dead, then alive).
    fake = FakeRun(rc={"has-session": [1, 0, 1, 0]})
    slept: list[float] = []
    sup = BoxSupervisor(_config(max_restart_retries=3), run=fake, sleep=slept.append)
    assert sup._self_heal() is True  # first death: recovers on attempt 2
    assert sup._self_heal() is True  # second death: fresh budget, recovers on attempt 2
    assert len(fake.sub_calls("new-session")) == 4  # 2 restart attempts per death
    assert slept == [0.5, 0.5]  # one backoff before each successful 2nd attempt


# ---------------------------------------------------------------------------
# on_agent_exit policy (E2c) — launch-intent-aware teardown vs self-heal.
# ---------------------------------------------------------------------------

def test_run_forever_teardown_ran_then_exited_returns_zero():
    # Foreground CLI 'teardown' REALITY: remain-on-exit is NOT armed, so on exit the
    # pane simply CLOSES and the session VANISHES — there is no dead-pane status to
    # read.  An agent that CAME UP (alive at startup) and then exited must close the
    # box with rc 0: a clean quit must NOT masquerade as a failure (the whole point
    # of dropping the dead pane).  No self-heal while a human-driven CLI is the driver.
    fake = FakeRun(
        # startup liveness="" (alive); tick1 has-session rc 1 (session vanished, no pane).
        rc={"has-session": [0, 1]},
        stdout={"display-message": [""]},
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 0
    assert restarts == []                       # never self-healed
    assert fake.sub_calls("new-session") == []  # and never restarted the agent


def test_run_forever_teardown_sole_agent_crash_also_returns_zero():
    # DOCUMENTED tradeoff of "no dead pane ever": with remain-on-exit off a sole-agent
    # CRASH is INDISTINGUISHABLE from a clean exit (both just vanish the session), so
    # it ALSO returns rc 0 — until a pipe-pane follow-up restores truthful crash codes.
    # (Modelled identically to a clean exit: came up, then session gone, no status.)
    fake = FakeRun(rc={"has-session": [0, 1]}, stdout={"display-message": [""]})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 0


def test_run_forever_teardown_honors_present_dead_pane_status():
    # DEFENSIVE: a teardown launch does not arm remain-on-exit, but if a stale/armed
    # pane IS somehow present carrying a #{pane_dead_status}, honor it VERBATIM rather
    # than override — so a real code is never discarded when it happens to exist.
    fake = FakeRun(
        rc={"has-session": [0, 0]},
        stdout={"display-message": ["", "42", "42"]},
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 42
    assert restarts == []
    assert fake.sub_calls("new-session") == []


def test_run_forever_teardown_echoes_present_pane_output_to_stdout(capsys):
    # DEFENSIVE: if a stale/armed pane is present on teardown, its captured output is
    # echoed to PID-1's stdout (podman logs) before the box closes so the host can
    # show WHY the agent died.  (In the normal remain-on-exit-off teardown the pane
    # has closed and this captures nothing; here a pane is mocked present.)
    fake = FakeRun(
        rc={"has-session": [0, 0]},
        stdout={
            "display-message": ["", "1", "1"],
            "capture-pane": "agent crashed: boom\n",
        },
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 1
    assert "agent crashed: boom" in capsys.readouterr().out


def test_run_forever_teardown_tolerates_empty_capture(capsys):
    # A capture that yields nothing (pane already gone / tmux hiccup) must NOT crash
    # teardown or fabricate output — the truthful exit code is still returned.
    fake = FakeRun(
        rc={"has-session": [0, 0]},
        stdout={"display-message": ["", "3", "3"]},  # capture-pane -> "" (default)
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 3
    assert capsys.readouterr().out == ""


def test_run_forever_self_heal_restarts_after_dead_pane():
    # E2d: the DEFAULT self-heal policy still restarts a DEAD pane (has-session rc 0
    # + a non-empty pane_dead_status).  restart_agent_session kill-sessions the dead
    # pane FIRST (so the fresh new-session can reuse the name), then new-sessions the
    # continue grammar, and the box recovers.
    fake = FakeRun(
        rc={"has-session": [0, 0, 0, 0]},
        # startup=live(""); tick1 liveness=dead("7"); _self_heal recheck=live("");
        # tick2 liveness=live("").
        stdout={"display-message": ["", "7", "", ""]},
    )
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert fake.sub_calls("new-session") == [_CONTINUE_CALL]
    assert fake.sub_calls("kill-session") == [
        ["tmux", "kill-session", "-t", "kanibako"]
    ]


def test_run_forever_teardown_initial_start_failure_returns_one():
    # Foreground 'teardown', NEVER-STARTED case: the INITIAL agent start fails
    # (new-session rc!=0), so the agent never came up (``started`` is False).  Even
    # with no dead pane, this is a TRUTHFUL FAILURE → the loop returns rc 1 so the box
    # closes and the host surfaces the start error (NOT the rc-0 clean-exit path — the
    # started/never-started distinction is exactly what keeps a real start failure
    # from masquerading as success).  No self-heal (design §86-88).
    fake = FakeRun(rc={"has-session": 1, "new-session": 1})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 1
    assert len(fake.sub_calls("new-session")) == 1  # one start attempt, no retry loop


def test_run_forever_self_heal_policy_does_not_teardown_on_agent_exit():
    # REGRESSION guard: the DEFAULT (self-heal) policy keeps E2b's always-on
    # behavior — a dead agent is RESTARTED with the continue grammar, NOT torn down.
    fake = FakeRun(rc={"has-session": [0, 1, 0, 0]})
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])  # default self-heal
    assert sup.config.on_agent_exit == "self-heal"
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert fake.sub_calls("new-session") == [_CONTINUE_CALL]


# ---------------------------------------------------------------------------
# surface-aware teardown (E2e) — CLI↔panel ref-count (principle B / FF-8).
# ---------------------------------------------------------------------------

def test_other_surface_attached_is_the_panel_slice():
    sup = BoxSupervisor(_config(), run=FakeRun())
    # The VS Code panel is the "other" surface (beyond the CLI's own tmux terminal).
    assert sup._other_surface_attached(_VS) is True
    assert sup._other_surface_attached(_BOTH) is True
    # A tmux terminal (the CLI's own surface) or nothing is NOT an "other" surface.
    assert sup._other_surface_attached(_TM) is False
    assert sup._other_surface_attached(_NONE) is False


def _script_snapshots(sup: BoxSupervisor, states: list[AttachState]) -> None:
    """Wire ``_snapshot`` to a fixed script of :class:`AttachState`s (last sticks).

    The panel-presence signal comes from :attr:`AttachState.vscode_server`, which
    :func:`snapshot_attach_state` derives from ``/proc`` cmdlines — awkward to vary
    per tick via construction — so tests drive the surface directly by scripting the
    snapshot, exactly as other tests stub ``restart_agent_session`` / ``_on_detach``.
    """
    seq = list(states)

    def fake_snapshot() -> AttachState:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    sup._snapshot = fake_snapshot  # type: ignore[method-assign]


def test_run_forever_teardown_stays_up_while_panel_attached_then_closes():
    # MUTATION-PROVE (E2e, the core test): under 'teardown', the agent is DEAD from
    # tick1 on.  tick1 has a panel attached (vscode_server=True) → the box must NOT
    # tear down (stay an agentless keep-alive, no restart); tick2 the panel is GONE
    # → the box tears down and returns the dead-pane code.
    #   snapshots: prev=panel, tick1=panel, tick2=no-surface.
    #   display-message (pane_dead_status) is consumed once per liveness check AND
    #   once per teardown re-read, so the script distinguishes WHEN teardown fires:
    #     startup=""(alive); tick1 liveness="1"(dead); tick2 liveness="3"(dead);
    #     tick2 teardown re-read="5" (last sticks).
    # A mutant that IGNORES the surface tears down at tick1: its tick1 teardown
    # re-read is the NEXT value after tick1's "1" liveness read → "3", so it returns
    # 3 (not 5).  A mutant that NEVER tears down keeps the keep-alive past tick2 →
    # stopped by the bounded-tick harness → returns 0 (not 5).  Only the truthful
    # code — no teardown at tick1, teardown at tick2 — returns exactly 5, so
    # asserting == 5 catches BOTH mutants in this one test.
    fake = FakeRun(rc={"has-session": 0}, stdout={"display-message": ["", "1", "3", "5"]})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    _script_snapshots(sup, [_VS, _VS, _NONE])
    _stop_after(sup, 5)  # bound a would-be never-tears-down mutant
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 5           # closed at tick2, with the tick2 code
    assert restarts == []                   # never self-healed while the panel was up
    assert fake.sub_calls("new-session") == []  # and never restarted the agent


def test_run_forever_teardown_no_surface_closes_immediately():
    # Regression (E2d intact): agent dead + NO other surface on the first tick →
    # tears down at once and returns the truthful dead-pane code (here 42).
    fake = FakeRun(rc={"has-session": 0}, stdout={"display-message": ["", "42", "42"]})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    _script_snapshots(sup, [_NONE, _NONE])
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 42
    assert restarts == []
    assert fake.sub_calls("new-session") == []


def test_run_forever_teardown_keepalive_logs_once_not_per_tick(caplog):
    # The agentless keep-alive state must log ONCE on entry, not every poll tick.
    import logging

    fake = FakeRun(rc={"has-session": 0}, stdout={"display-message": ["", "1"]})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    _script_snapshots(sup, [_VS])  # panel present on every snapshot (never detaches)
    _stop_after(sup, 4)            # several ticks of dead-agent + panel
    with caplog.at_level(logging.INFO, logger="kanibako.box_supervisor"):
        assert sup.run_forever() == 0  # never tears down; stopped by the harness
    entries = [r for r in caplog.records if "agentless keep-alive" in r.getMessage()]
    assert len(entries) == 1  # logged exactly once despite multiple keep-alive ticks


def test_run_forever_teardown_initial_start_failure_stays_up_with_panel():
    # Surface-aware failed INITIAL start: the agent never starts (new-session rc!=0),
    # but a panel is attached → the box does NOT close (agentless keep-alive), it keeps
    # polling.  Exactly ONE start attempt (the failed initial one); no self-heal restart.
    fake = FakeRun(rc={"has-session": 1, "new-session": 1})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake)
    _script_snapshots(sup, [_VS])  # panel present throughout
    _stop_after(sup, 3)
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 0                    # stayed up (no teardown return)
    assert len(fake.sub_calls("new-session")) == 1   # one start attempt, no retries
    assert restarts == []


# ---------------------------------------------------------------------------
# main / argparse.
# ---------------------------------------------------------------------------

def test_config_from_argv_default_on_agent_exit_is_self_heal():
    cfg = config_from_argv(["--session", "s", "--marker", "m", "--", "claude"])
    assert cfg.on_agent_exit == "self-heal"


def test_config_from_argv_parses_on_agent_exit_teardown():
    cfg = config_from_argv(
        ["--session", "s", "--marker", "m", "--on-agent-exit", "teardown",
         "--", "claude"]
    )
    assert cfg.on_agent_exit == "teardown"


def test_config_from_argv_rejects_unknown_on_agent_exit():
    with pytest.raises(SystemExit):
        config_from_argv(
            ["--session", "s", "--marker", "m", "--on-agent-exit", "bogus",
             "--", "claude"]
        )


def test_config_from_argv_parses_session_marker_and_start_argv():
    cfg = config_from_argv(
        ["--session", "kanibako", "--marker", _MARKER, "--",
         "claude", "--dangerously-skip-permissions"]
    )
    assert cfg.session == "kanibako"
    assert cfg.marker == _MARKER
    assert cfg.start_argv == ["claude", "--dangerously-skip-permissions"]
    # continue_argv defaults to a copy of start_argv
    assert cfg.continue_argv == ["claude", "--dangerously-skip-permissions"]
    assert cfg.continue_argv is not cfg.start_argv


def test_config_from_argv_continue_cmd_is_shlex_split():
    cfg = config_from_argv(
        ["--session", "s", "--marker", "m", "--continue-cmd", "claude --continue",
         "--poll", "1.5", "--max-retries", "5", "--", "claude"]
    )
    assert cfg.continue_argv == ["claude", "--continue"]
    assert cfg.poll_interval == 1.5
    assert cfg.max_restart_retries == 5


def test_config_from_argv_requires_trailing_argv():
    with pytest.raises(SystemExit):
        config_from_argv(["--session", "s", "--marker", "m"])


def test_config_from_argv_requires_session_and_marker():
    with pytest.raises(SystemExit):
        config_from_argv(["--", "claude"])


def test_main_wires_config_and_runs_the_loop(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run_forever(self: BoxSupervisor) -> int:
        seen["config"] = self.config
        return 0

    monkeypatch.setattr(BoxSupervisor, "run_forever", fake_run_forever)
    rc = main(["--session", "kanibako", "--marker", _MARKER, "--", "claude", "--continue"])
    assert rc == 0
    cfg = seen["config"]
    assert isinstance(cfg, SupervisorConfig)
    assert cfg.session == "kanibako"
    assert cfg.start_argv == ["claude", "--continue"]


# ---------------------------------------------------------------------------
# E2f — panel-watch: decide_panel (the pure state machine), the marker probe,
#       and the agent-independent `code` warm-up loop.
# ---------------------------------------------------------------------------

# The full decide_panel truth table, one row per distinct edge.  Two DISTINCT
# surface signals (principle B / the E2e FF-8 fix): `server` (the PANEL) gates
# SELF_HEAL_CLI; `any_attached` (panel OR tmux terminal) gates keep-alive/teardown.
# `server=True` implies `any_attached=True` (the panel IS a surface); `server=False,
# any=True` is a bare tmux terminal.  Each row's expected value differs from a
# neighbour that flips exactly ONE input, so the parametrization is mutation-proof.
@pytest.mark.parametrize(
    "tmux_alive,panel,server,any_attached,seen,expected",
    [
        # An agent IS running → hands-off (NONE), regardless of the rest.
        (True, PanelAgentState.NONE, False, False, True, PanelActionKind.NONE),
        (True, PanelAgentState.DEAD, True, True, True, PanelActionKind.NONE),   # tmux wins over dead marker + panel
        (False, PanelAgentState.ALIVE, False, False, True, PanelActionKind.NONE),
        (False, PanelAgentState.ALIVE, False, False, False, PanelActionKind.NONE),
        # panel DEAD + PANEL connected → self-heal the CLI agent (§89-96), seen irrelevant.
        (False, PanelAgentState.DEAD, True, True, True, PanelActionKind.SELF_HEAL_CLI),
        (False, PanelAgentState.DEAD, True, True, False, PanelActionKind.SELF_HEAL_CLI),
        # panel DEAD + NO panel: keep-alive while ANY surface (a terminal) stays;
        # else teardown-iff-seen.  A terminal (server=False, any=True) MUST keep the
        # box up (the FF-8-class bug: never tear down under an attached terminal).
        (False, PanelAgentState.DEAD, False, True, True, PanelActionKind.NONE),      # terminal attached → keep-alive
        (False, PanelAgentState.DEAD, False, False, True, PanelActionKind.TEARDOWN),  # no surface, seen → teardown
        (False, PanelAgentState.DEAD, False, False, False, PanelActionKind.NONE),     # grace (never seen)
        # panel NONE + PANEL connected → keep-alive (the panel will (re)bring an agent).
        (False, PanelAgentState.NONE, True, True, True, PanelActionKind.NONE),
        (False, PanelAgentState.NONE, True, True, False, PanelActionKind.NONE),
        # panel NONE + no panel: keep-alive while a terminal stays; teardown-iff-seen.
        (False, PanelAgentState.NONE, False, True, True, PanelActionKind.NONE),       # terminal attached → keep-alive
        (False, PanelAgentState.NONE, False, False, True, PanelActionKind.TEARDOWN),  # no surface, seen → teardown
        (False, PanelAgentState.NONE, False, False, False, PanelActionKind.NONE),     # grace
    ],
)
def test_decide_panel_table(tmux_alive, panel, server, any_attached, seen, expected):
    assert decide_panel(tmux_alive, panel, server, any_attached, seen).kind is expected


def test_decide_panel_seen_latch_flips_teardown_vs_grace():
    # Isolate the seen_surface input: identical no-live-agent/no-surface state,
    # only the latch differs → TEARDOWN vs NONE (the grace).  Mutation-proof for a
    # mutant that ignores seen_surface.
    assert decide_panel(
        False, PanelAgentState.NONE, False, False, True,
    ).kind is PanelActionKind.TEARDOWN
    assert decide_panel(
        False, PanelAgentState.NONE, False, False, False,
    ).kind is PanelActionKind.NONE


def test_decide_panel_server_gates_self_heal_vs_teardown():
    # Isolate the server (PANEL) input: DEAD panel, seen, no OTHER surface, only the
    # panel differs → SELF_HEAL_CLI (panel up) vs TEARDOWN (panel gone).  Mutation-
    # proof for a mutant that ignores vscode_server.
    assert decide_panel(
        False, PanelAgentState.DEAD, True, True, True,
    ).kind is PanelActionKind.SELF_HEAL_CLI
    assert decide_panel(
        False, PanelAgentState.DEAD, False, False, True,
    ).kind is PanelActionKind.TEARDOWN


def test_decide_panel_terminal_surface_prevents_teardown():
    # THE FF-8-class bug this fix closes: the CLI agent is dead and the PANEL is
    # closed (vscode_server=False), but a tmux TERMINAL is still attached
    # (any_attached=True) → must NOT tear down.  Keying teardown on the panel alone
    # (the pre-fix bug) would close the box out from under the terminal.
    assert decide_panel(
        False, PanelAgentState.DEAD, False, True, True,
    ).kind is PanelActionKind.NONE
    assert decide_panel(
        False, PanelAgentState.NONE, False, True, True,
    ).kind is PanelActionKind.NONE


# -- scan_marker_pids / newcomer_pids (pure-ish 4a helpers) ------------------

def test_scan_marker_pids_partitions_live_and_stale():
    live, stale = scan_marker_pids(
        "/d", list_pids=lambda _p: [10, 20, 30], pid_alive=lambda pid: pid != 20,
    )
    assert live == {10, 30}
    assert stale == {20}


def test_scan_marker_pids_drops_non_positive_and_tolerates_raising_probe():
    def alive(pid: int) -> bool:
        if pid == 40:
            raise OSError("kill(0) boom")
        return True

    # 0 / -1 dropped up front; a probe that raises for 40 skips ONLY that pid.
    live, stale = scan_marker_pids(
        "/d", list_pids=lambda _p: [0, -1, 40, 50], pid_alive=alive,
    )
    assert live == {50}
    assert stale == set()


def test_default_list_marker_pids_parses_filenames_and_tolerates_absent(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "123").write_text("123")
    (d / "456").write_text("456")
    (d / "not-a-pid").write_text("x")  # skipped (non-integer name)
    assert set(_default_list_marker_pids(str(d))) == {123, 456}
    # An ABSENT dir is tolerated as "no agents yet" (never raises).
    assert _default_list_marker_pids(str(tmp_path / "missing")) == []


def test_newcomer_pids_is_live_minus_own():
    assert newcomer_pids({1, 2, 3}, {2}) == {1, 3}
    assert newcomer_pids({5}, {5}) == set()
    assert newcomer_pids(set(), {9}) == set()


# -- panel_agent_state (dir enumeration; injectable lister + probe) -----------

def _panel_sup(
    *,
    markers_dir: str | None = "/run/kanibako/agents",
    marker_pids: list[int] | None = None,
    alive=lambda pid: True,
    raise_alive: bool = False,
    panes: str = "",
) -> BoxSupervisor:
    def list_pids(path: str) -> list[int]:
        return list(marker_pids or [])

    def pid_alive(pid: int) -> bool:
        if raise_alive:
            raise OSError("kill(0) boom")
        return alive(pid)

    return BoxSupervisor(
        _config(agent_markers_dir=markers_dir),
        run=FakeRun(stdout={"list-panes": panes}),
        list_marker_pids=list_pids,
        pid_alive=pid_alive,
    )


def test_panel_agent_state_none_when_no_markers_dir_configured():
    assert _panel_sup(markers_dir=None).panel_agent_state() is PanelAgentState.NONE


def test_panel_agent_state_none_when_dir_empty():
    assert _panel_sup(marker_pids=[]).panel_agent_state() is PanelAgentState.NONE


def test_panel_agent_state_alive_for_live_marker():
    assert _panel_sup(
        marker_pids=[777], alive=lambda pid: True,
    ).panel_agent_state() is PanelAgentState.ALIVE


def test_panel_agent_state_dead_for_stale_marker():
    assert _panel_sup(
        marker_pids=[777], alive=lambda pid: False,
    ).panel_agent_state() is PanelAgentState.DEAD


def test_panel_agent_state_tolerates_a_raising_probe():
    # A liveness probe that raises must never propagate (PID-1 immortality): the
    # per-pid skip leaves no live/stale marker → NONE.
    assert _panel_sup(
        marker_pids=[777], raise_alive=True,
    ).panel_agent_state() is PanelAgentState.NONE


def test_panel_agent_state_excludes_own_tmux_pane_marker():
    # A marker whose PID is the supervisor's OWN tmux pane (a self-healed CLI writes a
    # marker too) is NOT a panel agent → excluded, so a lone own-pane marker reads as
    # NONE, not ALIVE.  Proves the read side is over ONE scheme (no double-count).
    sup = _panel_sup(marker_pids=[555], alive=lambda pid: True, panes="555\n")
    assert sup.panel_agent_state() is PanelAgentState.NONE


# -- _own_agent_pids (the tmux pane PIDs = the supervisor's own agent) --------

def test_own_agent_pids_parses_list_panes_pane_pid():
    sup = BoxSupervisor(
        _config(), run=FakeRun(stdout={"list-panes": "100\n101\n"}),
    )
    assert sup._own_agent_pids() == {100, 101}
    assert sup._run.sub_calls("list-panes") == [  # type: ignore[attr-defined]
        ["tmux", "list-panes", "-s", "-t", "kanibako", "-F", "#{pane_pid}"]
    ]


def test_own_agent_pids_empty_when_no_session():
    # tmux non-zero (no session) → _tmux_output None → empty set (never raises).
    sup = BoxSupervisor(_config(), run=FakeRun(rc={"list-panes": 1}))
    assert sup._own_agent_pids() == set()


# -- the panel-watch loop (agent-independent `code` warm-up) ------------------

def _panel_watch_sup(
    fake: FakeRun, *, marker_pids=None, alive=lambda pid: True,
) -> BoxSupervisor:
    """A panel-watch supervisor with an injected markers lister + liveness probe."""
    return BoxSupervisor(
        _config(panel_watch=True, agent_markers_dir="/run/kanibako/agents"),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: list(marker_pids or []),
        pid_alive=alive,
    )


def test_panel_watch_startup_is_agentless_and_stays_up():
    # MUTATION-PROOF (the regression this arc closes): panel-watch startup starts NO
    # CLI agent.  A never-attached box (no surface, no marker) stays up through the
    # grace until the harness stops it — and NEVER emits a tmux new-session.
    fake = FakeRun(rc={"has-session": 1})  # no tmux agent present
    sup = _panel_watch_sup(fake, marker_pids=None)  # panel marker absent → NONE
    _script_snapshots(sup, [_NONE])
    slept = _stop_after(sup, 3)
    assert sup.run_forever() == 0
    assert fake.sub_calls("new-session") == []   # never started an agent
    assert len(slept) == 3                        # stayed up (grace) to the harness bound


def test_panel_watch_dead_marker_with_server_self_heals_a_cli_agent():
    # The §89-96 fallback: the panel agent DIED (stale marker) with the panel still
    # connected (vscode_server) → self-heal a CLI agent.  Stub _self_heal to record.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, marker_pids=[4242], alive=lambda pid: False)  # DEAD marker
    _script_snapshots(sup, [_VS])  # panel/server present throughout
    healed: list[int] = []
    sup._self_heal = lambda: (healed.append(1) or True)  # type: ignore[method-assign]
    _stop_after(sup, 1)
    assert sup.run_forever() == 0
    assert healed == [1]  # self-heal fired on the DEAD-marker + server tick


def test_panel_watch_live_panel_agent_is_hands_off():
    # A LIVE panel agent (marker names a live PID) → the loop never self-heals or
    # tears down; the panel is the sole agent.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, marker_pids=[999], alive=lambda pid: True)  # ALIVE marker
    _script_snapshots(sup, [_VS])
    healed: list[int] = []
    sup._self_heal = lambda: (healed.append(1) or True)  # type: ignore[method-assign]
    slept = _stop_after(sup, 3)
    assert sup.run_forever() == 0
    assert healed == []                          # never self-healed while the panel is live
    assert fake.sub_calls("new-session") == []
    assert len(slept) == 3                        # stayed up


def test_panel_watch_tears_down_after_last_surface_detaches():
    # All-gone-after-seen: prev has a panel (seen latches True); tick1 the panel is
    # GONE and the marker is absent → TEARDOWN returns 0.  MUTATION-PROOF: teardown
    # fires on tick1 BEFORE the first poll sleep, so `slept == []`; a mutant that
    # ignores the seen-latch (never tears down) would sleep to the harness bound.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, marker_pids=None)  # panel marker absent → NONE
    _script_snapshots(sup, [_VS, _NONE])
    healed: list[int] = []
    sup._self_heal = lambda: (healed.append(1) or True)  # type: ignore[method-assign]
    slept = _stop_after(sup, 5)  # bound a would-be never-tears-down mutant
    assert sup.run_forever() == 0
    assert slept == []           # tore down at tick1, before any poll sleep
    assert healed == []


def test_panel_watch_dead_marker_no_server_tears_down_without_self_heal():
    # DEAD marker but NO server (the surface detached) + seen → TEARDOWN, NOT
    # self-heal.  Proves self-heal REQUIRES the server surface.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, marker_pids=[4242], alive=lambda pid: False)  # DEAD marker
    _script_snapshots(sup, [_VS, _NONE])  # seen, then no surface
    healed: list[int] = []
    sup._self_heal = lambda: (healed.append(1) or True)  # type: ignore[method-assign]
    slept = _stop_after(sup, 5)
    assert sup.run_forever() == 0
    assert slept == []    # teardown at tick1 (surface gone, seen)
    assert healed == []   # DEAD marker but no server → no self-heal


def test_panel_watch_fires_detach_hook_on_surface_loss():
    # The DETACH hook (D's cred-writeback point) still fires on a surface loss in
    # panel-watch mode.  A LIVE panel keeps the box up so the hook is observable.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, marker_pids=[999], alive=lambda pid: True)  # ALIVE → hands-off
    _script_snapshots(sup, [_BOTH, _VS])  # tmux terminal detaches, panel stays
    fired: list[int] = []
    sup._on_detach = lambda: fired.append(1)  # type: ignore[method-assign]
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert fired == [1]  # exactly one detach hook when the terminal surface dropped


# -- increment 4a: LOG-ONLY newcomer detection (both loops) ------------------

def _capture_warnings(monkeypatch) -> list[str]:
    """Capture ``box_supervisor.log.warning`` messages (formatted) into a list."""
    msgs: list[str] = []

    def warn(msg, *args):
        msgs.append(msg % args if args else msg)

    monkeypatch.setattr(bs.log, "warning", warn)
    return msgs


def test_run_forever_detects_newcomer_log_only(monkeypatch):
    # A LIVE own agent (tmux pane 100) with a NEWCOMER marker (PID 200) on the session
    # (e.g. the VS Code panel auto-`--resume`).  4a LOGS the newcomer exactly ONCE
    # across ticks and takes NO action.
    fake = FakeRun(
        rc={"has-session": 0},
        stdout={"list-panes": "100\n", "display-message": ""},
    )
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents"),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [100, 200],  # own pane 100 + newcomer 200
        pid_alive=lambda pid: True,
    )
    _script_snapshots(sup, [_NONE])
    warnings = _capture_warnings(monkeypatch)
    _stop_after(sup, 3)  # several ticks; the newcomer must log ONCE, not per tick
    assert sup.run_forever() == 0
    hits = [m for m in warnings if "newcomer agent PID 200" in m]
    assert len(hits) == 1


def test_run_forever_no_newcomer_when_only_own_agent_marker(monkeypatch):
    # The own tmux agent writes a marker too (== its pane PID).  It must NOT be
    # mistaken for a newcomer — zero warnings.
    fake = FakeRun(rc={"has-session": 0}, stdout={"list-panes": "100\n", "display-message": ""})
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents"),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [100],  # only the own pane's marker
        pid_alive=lambda pid: True,
    )
    _script_snapshots(sup, [_NONE])
    warnings = _capture_warnings(monkeypatch)
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert [m for m in warnings if "newcomer" in m] == []


def test_run_forever_no_detection_without_markers_dir(monkeypatch):
    # Byte-unchanged path: no markers dir configured → detection is skipped entirely
    # (no list-panes probe, no warnings).
    fake = FakeRun(rc={"has-session": 0}, stdout={"display-message": ""})
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])  # agent_markers_dir None
    _script_snapshots(sup, [_NONE])
    warnings = _capture_warnings(monkeypatch)
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert [m for m in warnings if "newcomer" in m] == []
    assert fake.sub_calls("list-panes") == []  # no own-pid probe when disabled


def test_panel_watch_detects_second_panel_as_newcomer_log_only(monkeypatch):
    # Panel-watch: the fronted panel (PID 900) is the incumbent; a SECOND live non-own
    # marker (PID 901, a concurrent resume) is the newcomer.  LOG-ONLY, once.
    fake = FakeRun(rc={"has-session": 1})  # no tmux agent (agentless warm)
    sup = _panel_watch_sup(fake, marker_pids=[900, 901], alive=lambda pid: True)
    _script_snapshots(sup, [_VS])
    warnings = _capture_warnings(monkeypatch)
    _stop_after(sup, 3)
    assert sup.run_forever() == 0
    # The lower PID latches as the fronted incumbent → only 901 is a newcomer, once.
    assert [m for m in warnings if "newcomer agent PID 901" in m] == [
        "newcomer agent PID 901 detected on session "
        "(increment 4a: detection only, no action)"
    ]
    assert [m for m in warnings if "newcomer agent PID 900" in m] == []


def test_4a_detection_takes_no_destructive_or_signal_action():
    # OVER-REACH GUARD: with a LIVE own agent + a newcomer marker, 4a must take NO
    # eviction/signal op — no kill_agent_session, no _self_heal, no tmux
    # kill-session/kill-pane/send-keys, and it never signals a process (the injected
    # ``kill`` is a spy that FAILS the test if called with any nonzero signal).
    fake = FakeRun(
        rc={"has-session": 0},
        stdout={"list-panes": "100\n", "display-message": ""},
    )
    signalled: list[tuple[int, int]] = []

    def spy_kill(pid, sig):
        signalled.append((pid, sig))
        if sig != 0:
            raise AssertionError(f"4a signalled pid {pid} with signal {sig}")

    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents"),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [100, 200],  # own + newcomer
        pid_alive=lambda pid: True,
        kill=spy_kill,
    )
    killed: list[int] = []
    healed: list[int] = []
    sup.kill_agent_session = lambda: killed.append(1)  # type: ignore[method-assign]
    sup._self_heal = lambda: (healed.append(1) or True)  # type: ignore[method-assign]

    _script_snapshots(sup, [_NONE])
    _stop_after(sup, 3)
    assert sup.run_forever() == 0
    # No eviction/handoff primitive fired (4a is detection substrate ONLY).
    assert killed == []
    assert healed == []
    assert fake.sub_calls("kill-session") == []
    assert fake.sub_calls("kill-pane") == []
    assert fake.sub_calls("send-keys") == []
    # And no real signal was ever sent (only signal 0 liveness, if any).
    assert all(sig == 0 for _pid, sig in signalled)


# -- increment 4b: single-writer ENFORCEMENT (takeover; grace + pause + evict) -

def _takeover_events_sup(*, own_out="100\n", grace=7.0):
    """A run_forever-mode supervisor instrumented to record the takeover SEQUENCE.

    Every relevant seam appends a tagged event to a shared list, so a single ordered
    log captures the SIGSTOP / send-keys / grace / process-group-kill / SIGCONT order:
    the tmux ``run`` records its subcommand, ``sleep`` records the grace, and the
    injected ``kill`` / ``killpg`` / ``getpgid`` primitives are fakes.  ``run``
    returns *own_out* for ``list-panes`` so :meth:`kill_agent_session` reaps that pane.
    """
    events: list[tuple] = []

    def run(args, **_kw):
        assert args[0] == "tmux"
        sub = args[1]
        events.append(("tmux", sub))
        out = own_out if sub == "list-panes" else ""
        return subprocess.CompletedProcess(args, returncode=0, stdout=out, stderr="")

    sup = BoxSupervisor(
        _config(
            agent_markers_dir="/run/kanibako/agents",
            session_takeover=True,
            takeover_grace=grace,
            send_keys_retries=1,
        ),
        run=run,
        sleep=lambda s: events.append(("sleep", s)),
        proc_cmdlines=[],
        kill=lambda pid, sig: events.append(("kill", pid, sig)),
        killpg=lambda pgid, sig: events.append(("killpg", pgid, sig)),
        getpgid=lambda pid: pid,
    )
    return sup, events


def test_takeover_sequence_order_pause_headsup_grace_pgkill_resume():
    # THE core 4b assertion: with the flag ON, a newcomer (200) over a live pane
    # incumbent (100) runs the takeover in the EXACT order
    #   SIGSTOP(200) → send-keys(100, heads-up) → grace → process-group-kill(100) →
    #   SIGCONT(200)
    # and the kill is a process GROUP of the PANE incumbent, never the bare marker.
    sup, events = _takeover_events_sup(grace=7.0)
    assert sup._takeover({100}, {200}) is True

    def idx(pred) -> int:
        return next(i for i, e in enumerate(events) if pred(e))

    i_stop = idx(lambda e: e == ("kill", 200, signal.SIGSTOP))
    i_send = idx(lambda e: e == ("tmux", "send-keys"))
    i_grace = idx(lambda e: e[0] == "sleep")
    i_killpg = idx(lambda e: e == ("killpg", 100, signal.SIGTERM))
    i_killsession = idx(lambda e: e == ("tmux", "kill-session"))
    i_cont = idx(lambda e: e == ("kill", 200, signal.SIGCONT))
    # Full ordering: pause → heads-up → grace → evict(pgkill then session) → resume.
    assert i_stop < i_send < i_grace < i_killpg < i_killsession < i_cont
    # The grace used the configured window.
    assert ("sleep", 7.0) in events
    # The evict is a process-GROUP kill of the PANE incumbent (100), never the marker.
    assert ("killpg", 100, signal.SIGTERM) in events
    assert all(not (e[0] == "killpg" and e[1] == 200) for e in events)
    # The newcomer (200) is ONLY ever SIGSTOP/SIGCONT'd — never killed / group-killed.
    assert all(
        e[2] in (signal.SIGSTOP, signal.SIGCONT)
        for e in events
        if e[0] == "kill" and e[1] == 200
    )


def test_takeover_returns_true_and_run_forever_hands_off_to_panel_watch():
    # Flag ON at the LOOP level: a live CLI incumbent (pane 100) + a panel newcomer
    # (marker 200) → run_forever performs the takeover then hands off to the agentless
    # panel-watch keep-alive (self-heal-to-CLI on the newcomer's death).
    fake = FakeRun(
        rc={"has-session": 0},
        stdout={"list-panes": "100\n", "display-message": ""},
    )
    sup = BoxSupervisor(
        _config(
            agent_markers_dir="/run/kanibako/agents",
            session_takeover=True,
            takeover_grace=0.0,
        ),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [100, 200],  # own pane 100 + newcomer 200
        pid_alive=lambda pid: True,
        kill=lambda pid, sig: None,
        killpg=lambda pgid, sig: None,
        getpgid=lambda pid: pid,
    )
    handoff: list[int] = []
    sup._run_panel_watch = lambda: (handoff.append(1) or 0)  # type: ignore[method-assign]
    _script_snapshots(sup, [_NONE])
    _stop_after(sup, 5)  # safety bound if the takeover somehow does not fire
    assert sup.run_forever() == 0
    assert handoff == [1]  # handed off to the agentless keep-alive exactly once


def test_takeover_does_not_fire_without_a_pane_incumbent():
    # SAFETY: never evict on a bare marker.  A newcomer marker with NO own pane
    # (list-panes empty → own == {}) must NOT trigger a takeover (nothing legitimate
    # to hand off), and NO signals fire — it falls through to the loop unchanged.
    fake = FakeRun(rc={"has-session": 1}, stdout={"list-panes": "", "display-message": ""})
    signals: list = []
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents", session_takeover=True),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [200],  # a marker but NO own pane
        pid_alive=lambda pid: True,
        kill=lambda pid, sig: signals.append((pid, sig)),
        killpg=lambda pgid, sig: signals.append(("pg", pgid, sig)),
    )
    fired: list[int] = []
    sup._takeover = lambda *a: (fired.append(1) or True)  # type: ignore[method-assign]
    _script_snapshots(sup, [_NONE])
    _stop_after(sup, 2)
    # No pane incumbent → self-heal path fires instead (agent dead), but crucially the
    # takeover never runs.  Stub _self_heal so the loop terminates deterministically.
    sup._self_heal = lambda: True  # type: ignore[method-assign]
    assert sup.run_forever() == 0
    assert fired == []       # takeover never invoked without a pane incumbent
    assert signals == []     # and no stray signal was sent


def test_flag_off_is_4a_log_only_no_signals(monkeypatch):
    # FLAG-OFF == 4a proof: the SAME newcomer scenario (live pane 100 + newcomer 200)
    # with the flag OFF (default) LOGS the newcomer (4a detection) and takes ZERO
    # signal/kill ops — no kill, no killpg, no send-keys, no kill-session.
    fake = FakeRun(
        rc={"has-session": 0},
        stdout={"list-panes": "100\n", "display-message": ""},
    )
    signals: list = []
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents"),  # session_takeover default False
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [100, 200],
        pid_alive=lambda pid: True,
        kill=lambda pid, sig: signals.append(("kill", pid, sig)),
        killpg=lambda pgid, sig: signals.append(("killpg", pgid, sig)),
    )
    warnings = _capture_warnings(monkeypatch)
    _script_snapshots(sup, [_NONE])
    _stop_after(sup, 3)
    assert sup.run_forever() == 0
    # Detection still logged the newcomer exactly once (byte-identical to 4a).
    assert len([m for m in warnings if "newcomer agent PID 200" in m]) == 1
    # ...but NOTHING destructive/signalling fired.
    assert signals == []
    assert fake.sub_calls("send-keys") == []
    assert fake.sub_calls("kill-session") == []


def test_takeover_error_before_evict_resumes_newcomer_and_does_not_kill():
    # ERROR-PATH SAFETY: a failure BETWEEN the SIGSTOP and the evict (here the heads-up
    # raises) ⇒ the paused newcomer is SIGCONT'd (never left frozen) and the incumbent
    # is NOT killed — the takeover returns False.
    fake = FakeRun(stdout={"list-panes": "100\n"})
    signals: list = []
    killpg_calls: list = []
    grace: list[float] = []
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents", session_takeover=True),
        run=fake,
        sleep=grace.append,
        proc_cmdlines=[],
        kill=lambda pid, sig: signals.append((pid, sig)),
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
        getpgid=lambda pid: pid,
    )

    def boom() -> bool:
        raise RuntimeError("heads-up send-keys blew up")

    sup._send_takeover_heads_up = boom  # type: ignore[method-assign]

    assert sup._takeover({100}, {200}) is False  # NO takeover
    # The newcomer was paused THEN resumed (never left frozen).
    assert (200, signal.SIGSTOP) in signals
    assert (200, signal.SIGCONT) in signals
    # The incumbent was NOT evicted (no process-group kill, no kill-session).
    assert killpg_calls == []
    assert fake.sub_calls("kill-session") == []
    # The grace never elapsed (we failed before it).
    assert grace == []


def test_takeover_aborts_when_newcomer_gone_before_pause():
    # If every newcomer vanished before it could be paused (SIGSTOP → ProcessLookupError)
    # there is nothing to hand the session to → NO eviction of the legitimate incumbent.
    fake = FakeRun(stdout={"list-panes": "100\n"})

    def gone(_pid, _sig):
        raise ProcessLookupError

    killpg_calls: list = []
    sup = BoxSupervisor(
        _config(agent_markers_dir="/run/kanibako/agents", session_takeover=True),
        run=fake,
        proc_cmdlines=[],
        kill=gone,
        killpg=lambda pgid, sig: killpg_calls.append(1),
        getpgid=lambda pid: pid,
    )
    assert sup._takeover({100}, {200}) is False
    assert killpg_calls == []                    # incumbent NOT evicted
    assert fake.sub_calls("kill-session") == []


def test_panel_watch_reverse_direction_is_log_only_even_with_flag_on(monkeypatch):
    # DIRECTIONAL: in panel-watch (a live-panel incumbent) a newcomer has NO panel
    # injection vector → it stays LOG-ONLY (deferred) even with the takeover flag ON.
    # No SIGSTOP/SIGCONT, no process-group kill, no send-keys / kill-session.
    fake = FakeRun(rc={"has-session": 1})  # agentless warm (no tmux agent)
    signals: list = []
    sup = BoxSupervisor(
        _config(
            panel_watch=True,
            agent_markers_dir="/run/kanibako/agents",
            session_takeover=True,
        ),
        run=fake,
        proc_cmdlines=[],
        list_marker_pids=lambda _p: [900, 901],  # 900 latches incumbent, 901 newcomer
        pid_alive=lambda pid: True,
        kill=lambda pid, sig: signals.append((pid, sig)),
        killpg=lambda pgid, sig: signals.append(("pg", pgid, sig)),
    )
    warnings = _capture_warnings(monkeypatch)
    _script_snapshots(sup, [_VS])
    _stop_after(sup, 3)
    assert sup.run_forever() == 0
    # The newcomer is LOGGED (deferred), never signalled or evicted.
    assert [m for m in warnings if "newcomer agent PID 901" in m] != []
    assert signals == []
    assert fake.sub_calls("send-keys") == []
    assert fake.sub_calls("kill-session") == []


def test_kill_agent_session_reaps_the_pane_process_group():
    # The evict primitive reaps the pane agent's PROCESS GROUP (child-kill-with-parent)
    # BEFORE killing the tmux session — so no orphaned subagent survives.
    fake = FakeRun(stdout={"list-panes": "100 101\n"})
    killed_groups: list[tuple[int, int]] = []
    sup = BoxSupervisor(
        _config(),
        run=fake,
        getpgid=lambda pid: pid,
        killpg=lambda pgid, sig: killed_groups.append((pgid, sig)),
    )
    sup.kill_agent_session()
    # Both pane process groups reaped with SIGTERM...
    assert killed_groups == [(100, signal.SIGTERM), (101, signal.SIGTERM)]
    # ...then the tmux session killed.
    assert fake.sub_calls("kill-session") == [["tmux", "kill-session", "-t", "kanibako"]]


def test_kill_agent_session_tolerates_a_dead_pane_group():
    # A pane whose process already exited (killpg → ProcessLookupError) is a tolerant
    # no-op; the tmux kill-session still runs.  PID-1 must never die on a kill hiccup.
    fake = FakeRun(stdout={"list-panes": "100\n"})

    def dead(_pgid, _sig):
        raise ProcessLookupError

    sup = BoxSupervisor(_config(), run=fake, getpgid=lambda pid: pid, killpg=dead)
    sup.kill_agent_session()  # must not raise
    assert fake.sub_calls("kill-session") == [["tmux", "kill-session", "-t", "kanibako"]]


def test_kill_process_group_refuses_pid_zero_never_kills_supervisor_group():
    # SAFETY GUARD: a stray pid 0/1 must NEVER reach killpg — getpgid(0) aliases
    # the SUPERVISOR's own group, so killpg on it would SIGTERM PID-1 → box death.
    killpg_calls: list = []
    sup = BoxSupervisor(
        _config(), run=FakeRun(),
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )
    assert sup._kill_process_group(0, signal.SIGTERM) is False
    assert sup._kill_process_group(1, signal.SIGTERM) is False
    assert killpg_calls == []  # the group-kill primitive was NEVER reached


def test_kill_process_group_refuses_supervisor_own_group():
    # SAFETY GUARD: even a >1 pid whose pgid resolves to the supervisor's OWN group
    # (a mis-resolution) is refused — never escalate a group-kill to PID-1's group.
    killpg_calls: list = []
    sup = BoxSupervisor(
        _config(), run=FakeRun(),
        getpgrp=lambda: 4242,
        getpgid=lambda _pid: 4242,  # pane pgid == our group
        killpg=lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )
    assert sup._kill_process_group(100, signal.SIGTERM) is False
    assert killpg_calls == []


def test_process_ops_reach_the_os_only_through_the_injected_seam(monkeypatch):
    # BY CONSTRUCTION, not by convention: every process-touching op the supervisor
    # performs — the 4b SIGSTOP/SIGCONT, the pane process-group evict, the PID-1 reap —
    # goes through an INJECTED primitive.  The real ``os`` signal ops and the module
    # reaper are booby-trapped here, so a future direct call fails THIS test instead of
    # firing a real signal inside someone's unit run.
    def trap(name):
        def _boom(*_a, **_kw):
            raise AssertionError(f"box_supervisor reached the real {name}")
        return _boom

    for op in ("kill", "killpg", "getpgid", "getpgrp"):
        monkeypatch.setattr(bs.os, op, trap(f"os.{op}"))
    monkeypatch.setattr(bs, "reap_zombie_children", trap("module reap_zombie_children"))

    events: list[tuple] = []
    fake = FakeRun(
        rc={"has-session": 0},
        stdout={"list-panes": "100\n", "display-message": ""},
    )
    sup = BoxSupervisor(
        _config(
            agent_markers_dir="/run/kanibako/agents",
            session_takeover=True,
            takeover_grace=0.0,
        ),
        run=fake,
        sleep=lambda _s: None,
        proc_cmdlines=[],
        kill=lambda pid, sig: events.append(("kill", pid, sig)),
        killpg=lambda pgid, sig: events.append(("killpg", pgid, sig)),
        getpgid=lambda pid: pid,
        getpgrp=lambda: 4242,  # never a pane's pgid → the safety refusal stays clear
        reap=lambda: (events.append(("reap",)), 0)[1],
    )
    # The 4b sequence: pause, evict the incumbent's GROUP, resume — all on the fakes.
    assert sup._takeover({100}, {200}) is True
    assert ("kill", 200, signal.SIGSTOP) in events
    assert ("killpg", 100, signal.SIGTERM) in events
    assert ("kill", 200, signal.SIGCONT) in events
    # ...and the loop's PID-1 duty lands on the injected reap, not the module function.
    events.clear()
    _script_snapshots(sup, [_NONE])
    _stop_after(sup, 1)
    assert sup.run_forever() == 0
    assert ("reap",) in events


def test_config_from_argv_defaults_session_takeover_off():
    cfg = config_from_argv(["--session", "s", "--marker", "m", "--", "claude"])
    assert cfg.session_takeover is False
    assert cfg.takeover_grace == 5.0


def test_config_from_argv_parses_session_takeover_and_grace():
    cfg = config_from_argv(
        ["--session", "s", "--marker", "m", "--session-takeover",
         "--takeover-grace", "12.5", "--", "claude"]
    )
    assert cfg.session_takeover is True
    assert cfg.takeover_grace == 12.5


# -- config_from_argv (panel-watch flags) ------------------------------------

def test_config_from_argv_panel_watch_allows_no_trailing_agent_argv():
    # Panel-watch starts NO agent, so an EMPTY start_argv is allowed (the "no agent
    # argv" error is suppressed); --continue-cmd carries the self-heal grammar.
    cfg = config_from_argv(
        ["--session", "kanibako", "--marker", _MARKER, "--panel-watch",
         "--agent-markers-dir", "/run/kanibako/agents",
         "--continue-cmd", "claude --continue"]
    )
    assert cfg.panel_watch is True
    assert cfg.agent_markers_dir == "/run/kanibako/agents"
    assert cfg.start_argv == []
    assert cfg.continue_argv == ["claude", "--continue"]


def test_config_from_argv_defaults_panel_watch_off_and_no_markers_dir():
    cfg = config_from_argv(["--session", "s", "--marker", "m", "--", "claude"])
    assert cfg.panel_watch is False
    assert cfg.agent_markers_dir is None
    assert cfg.creds_flag is None  # D: absent --creds-flag -> _on_detach no-ops


def test_config_from_argv_threads_creds_flag():
    # D Part 1: --creds-flag flows into the config so _on_detach signals on detach.
    cfg = config_from_argv(
        ["--session", "s", "--marker", "m",
         "--creds-flag", "/home/agent/.kanibako/creds-dirty", "--", "claude"]
    )
    assert cfg.creds_flag == "/home/agent/.kanibako/creds-dirty"


def test_config_from_argv_non_panel_watch_still_requires_trailing_argv():
    # The empty-argv relaxation is SCOPED to --panel-watch; the E2b path still errors.
    with pytest.raises(SystemExit):
        config_from_argv(["--session", "s", "--marker", "m"])


# ---------------------------------------------------------------------------
# PYTHONPATH scrub (the host-mount entry must not reach the agent/tmux children).
# ---------------------------------------------------------------------------

def test_scrub_pythonpath_drops_var_when_only_mount_root():
    from kanibako.box_supervisor import (
        KANIBAKO_PKG_MOUNT_ROOT,
        scrub_bootstrap_pythonpath,
    )

    env = {"PYTHONPATH": KANIBAKO_PKG_MOUNT_ROOT}
    scrub_bootstrap_pythonpath(env)
    # Nothing else remained -> the var is dropped entirely, not left empty.
    assert "PYTHONPATH" not in env


def test_scrub_pythonpath_preserves_other_entries():
    from kanibako.box_supervisor import (
        KANIBAKO_PKG_MOUNT_ROOT,
        scrub_bootstrap_pythonpath,
    )

    env = {"PYTHONPATH": f"{KANIBAKO_PKG_MOUNT_ROOT}:/opt/other:/x/y"}
    scrub_bootstrap_pythonpath(env)
    # Exactly the mount-root element is removed; the image's own entries survive.
    assert env["PYTHONPATH"] == "/opt/other:/x/y"


def test_scrub_pythonpath_removes_mount_root_in_any_position():
    from kanibako.box_supervisor import (
        KANIBAKO_PKG_MOUNT_ROOT,
        scrub_bootstrap_pythonpath,
    )

    env = {"PYTHONPATH": f"/a:{KANIBAKO_PKG_MOUNT_ROOT}:/b"}
    scrub_bootstrap_pythonpath(env)
    assert env["PYTHONPATH"] == "/a:/b"


def test_scrub_pythonpath_noop_when_unset():
    from kanibako.box_supervisor import scrub_bootstrap_pythonpath

    env: dict[str, str] = {}
    scrub_bootstrap_pythonpath(env)
    assert env == {}


# ---------------------------------------------------------------------------
# GUARD: box_supervisor's import chain must stay third-party-free (LOAD-BEARING).
# ---------------------------------------------------------------------------

def test_box_supervisor_import_chain_is_stdlib_only():
    """box_supervisor + its kanibako.* import chain must import ONLY stdlib.

    WHY THIS GUARD EXISTS — do NOT weaken it without understanding the consequence:
    the box supervisor is exec'd as box PID-1 from the HOST kanibako package that
    ``commands/start.py`` bind-mounts read-only at ``KANIBAKO_PKG_MOUNT_ROOT`` and
    injects as PYTHONPATH.  That mount carries ONLY the kanibako package SOURCE — none
    of the host venv's third-party dependencies (PyYAML, packaging, ...).  So every
    module reached while importing ``kanibako.box_supervisor`` must resolve with NO
    third-party package present: it may import the stdlib, or an ALLOWLISTED
    intra-kanibako module that is ITSELF stdlib-only.  The instant a future edit adds
    e.g. ``import yaml`` here (or pulls in a kanibako module that imports a third-party
    package), the supervisor would ImportError as PID-1 on a published image and EVERY
    real launch would silently degrade to the bare-shell fallback (no agent).  This
    walk fails LOUDLY at that moment, naming the offending module + import, so the
    author extends the mount contract (add the dep to the image) or the allowlist
    deliberately — never by accident.
    """
    import ast
    import importlib.util
    import sys
    from pathlib import Path

    # The intra-kanibako modules the chain is ALLOWED to reach today.  ``kanibako`` is
    # the package __init__ (implicitly executed when a submodule is imported); the
    # other three are the module + its two direct, stdlib-only helpers.  Each is
    # verified stdlib-only by the recursion below — this set only bounds WHICH kanibako
    # modules may participate, so an accidental new intra-kanibako dependency (which
    # might drag in third-party code) trips the guard.
    allowed_kanibako = {
        "kanibako",
        "kanibako.box_supervisor",
        "kanibako.box_lifecycle",
        "kanibako.log",
    }

    def source_of(mod_name: str) -> "str | None":
        spec = importlib.util.find_spec(mod_name)
        if spec is None or spec.origin in (None, "built-in", "frozen"):
            return None
        origin = spec.origin
        if not origin.endswith(".py"):
            return None
        return Path(origin).read_text()

    def ancestors(mod_name: str) -> "list[str]":
        """Package __init__ modules implicitly executed when importing *mod_name*."""
        parts = mod_name.split(".")
        return [".".join(parts[:i]) for i in range(1, len(parts))]

    visited: set[str] = set()
    external_tops: set[str] = set()
    queue = ["kanibako.box_supervisor"]
    while queue:
        mod = queue.pop()
        if mod in visited:
            continue
        visited.add(mod)
        # Importing this module implicitly runs its ancestor package __init__s.
        for anc in ancestors(mod):
            assert anc in allowed_kanibako, (
                f"box_supervisor import chain reaches non-allowlisted kanibako "
                f"package {anc!r} (ancestor of {mod!r}); see this test's docstring"
            )
            if anc not in visited:
                queue.append(anc)
        src = source_of(mod)
        if src is None:
            continue
        tree = ast.parse(src, filename=mod)
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # No relative imports are expected in this chain; a level>0 import
                # would resolve against the package and is flagged here so it cannot
                # sneak a dependency past the walk.
                assert node.level == 0, (
                    f"unexpected relative import in {mod!r} (level={node.level}); "
                    "the guard walks only absolute imports"
                )
                base = node.module or ""
                if base:
                    candidates.append(base)
                    # `from pkg import sub` may name a SUBMODULE (not just an attr);
                    # include the candidate so a kanibako submodule is followed.
                    candidates += [f"{base}.{a.name}" for a in node.names]
            for name in candidates:
                top = name.split(".")[0]
                if top == "kanibako":
                    # An intra-kanibako reference.  Only FOLLOW it if it resolves to a
                    # real module (``kanibako.log.get_logger`` is an attribute, not a
                    # module, and find_spec raises for it — skip those).
                    try:
                        spec = importlib.util.find_spec(name)
                    except ModuleNotFoundError:
                        spec = None
                    if spec is None:
                        continue
                    assert name in allowed_kanibako, (
                        f"box_supervisor import chain reaches non-allowlisted "
                        f"kanibako module {name!r} (imported by {mod!r}); adding it "
                        "may drag third-party deps into box PID-1 — see docstring"
                    )
                    if name not in visited:
                        queue.append(name)
                else:
                    external_tops.add(top)

    # Every non-kanibako top-level module the chain imports must be stdlib.
    non_stdlib = sorted(t for t in external_tops if t not in sys.stdlib_module_names)
    assert not non_stdlib, (
        f"box_supervisor import chain imports non-stdlib package(s) {non_stdlib}; "
        "the host-package mount carries NO third-party deps, so box PID-1 would "
        "ImportError and every launch would degrade to the bare-shell fallback — "
        "see this test's docstring"
    )


# --- zombie-aware liveness probe + PID-1 child reaping (bifrost defect) ------
#
# A panel process orphaned onto supervisor PID-1 that then dies becomes a
# ZOMBIE; ``os.kill(pid, 0)`` answers True for zombies, so the marker probe
# read ALIVE forever and both SELF_HEAL_CLI and TEARDOWN wedged (bifrost
# repro: ``[sleep] <defunct>`` PPID 1, 60+ s silent).  Fix = both ends:
# the probe treats state ``Z`` as dead, and the supervise ticks reap.
#
# The zombie-forking tests work because THIS TEST PROCESS is the parent: we
# ``os.fork()`` a child that ``os._exit``s immediately and deliberately do NOT
# ``waitpid`` it — the kernel keeps it as OUR zombie (visible in /proc with
# state Z) until the test reaps it in ``finally`` (so no zombie outlives the
# test either way).


#: Deadline (seconds) for the zombie-test polls below.  Generous on purpose:
#: under FULL-SUITE CPU load a freshly forked child can take many milliseconds
#: to be scheduled at all, and these tests must hold under that load.
_ZOMBIE_DEADLINE = 5.0


def _poll_until(cond, *, deadline: float = _ZOMBIE_DEADLINE, interval: float = 0.01) -> bool:
    """Poll *cond* until truthy or *deadline* seconds elapse; True iff it held.

    The sanctioned wait shape for the zombie tests: condition-first (no sleep
    when the condition already holds), then short interval sleeps up to a hard
    deadline — never a bare sleep, never an unbounded spin.
    """
    end = _time.monotonic() + deadline
    while True:
        if cond():
            return True
        if _time.monotonic() >= end:
            return False
        _time.sleep(interval)


def _fork_zombie() -> int:
    """Fork a child that exits immediately; return its PID WITHOUT reaping it.

    GUARANTEES the child is genuinely in state Z before returning (or fails the
    test loudly).  ⚑ Deadline-poll, NOT a sleepless iteration spin: the old
    ``for _ in range(200)`` stat-read spin burned out in microseconds, before a
    loaded scheduler ever ran the child's ``_exit`` — then silently returned a
    STILL-LIVE pid.  That was the root of both observed full-suite flakes: a
    reap pass legitimately saw fewer zombies than forked (``assert 2 >= 3``),
    and ``_default_pid_alive`` read the not-yet-exited child as alive.
    """
    pid = _os.fork()
    if pid == 0:  # child
        _os._exit(0)
    # Parent: wait until the kernel actually shows the child as a zombie.
    if not _poll_until(lambda: bs._proc_stat_state(pid) == "Z"):
        state = bs._proc_stat_state(pid)
        _reap_quietly(pid)  # do not leak the child past the failure
        pytest.fail(
            f"_fork_zombie: child {pid} did not reach state Z within "
            f"{_ZOMBIE_DEADLINE}s (last observed state: {state!r})"
        )
    return pid


def _reap_quietly(pid: int) -> None:
    try:
        _os.waitpid(pid, 0)
    except ChildProcessError:
        pass  # something (the code under test) already reaped it — fine


@pytest.mark.skipif(not _Path("/proc").is_dir(), reason="needs /proc (Linux)")
class TestZombieAwareProbe:
    def test_real_zombie_reads_dead(self):
        """A REAL zombie child: kill-0 says it exists, the probe must say DEAD."""
        pid = _fork_zombie()
        try:
            _os.kill(pid, 0)  # precondition: kill-0 CAN see it (the old bug)
            assert bs._proc_stat_state(pid) == "Z"
            assert bs._default_pid_alive(pid) is False
        finally:
            _reap_quietly(pid)

    def test_live_process_reads_alive(self):
        """Sanity: a genuinely live PID (our own) still reads alive."""
        assert bs._default_pid_alive(_os.getpid()) is True

    def test_never_existed_pid_reads_dead(self):
        # PID 2^22+1 is above the default pid_max on most systems; if it does
        # exist the kill-0 path still answers correctly, so probe our own
        # guaranteed-dead child instead for determinism.
        pid = _fork_zombie()
        _reap_quietly(pid)  # fully reaped → the PID no longer exists
        assert bs._default_pid_alive(pid) is False

    def test_unreadable_stat_falls_back_to_kill0_verdict(self, monkeypatch):
        """stat unreadable/unparseable (None) → keep the kill-0 verdict (True
        for an existing process), never raise."""
        monkeypatch.setattr(bs, "_proc_stat_state", lambda pid: None)
        assert bs._default_pid_alive(_os.getpid()) is True


class TestParseStatState:
    def test_plain_comm(self):
        assert bs._parse_stat_state("123 (sleep) Z 1 123 0 0 -1") == "Z"

    def test_comm_with_spaces_and_parens(self):
        """comm is process-controlled and may contain ``) (`` — the state is
        the first field after the LAST ')'."""
        line = "42 (a) (b R fake) S 1 42 0"
        assert bs._parse_stat_state(line) == "S"

    def test_comm_containing_zombie_lookalike(self):
        # a comm that CONTAINS " Z " must not fool the parser.
        line = "7 (evil Z name) R 1 7 0"
        assert bs._parse_stat_state(line) == "R"

    def test_no_parens_is_none(self):
        assert bs._parse_stat_state("garbage with no parens") is None

    def test_nothing_after_paren_is_none(self):
        assert bs._parse_stat_state("9 (comm)") is None


@pytest.mark.skipif(not _Path("/proc").is_dir(), reason="needs /proc (Linux)")
class TestReapZombieChildren:
    def test_drains_multiple_zombies(self):
        pids = [_fork_zombie() for _ in range(3)]
        try:
            # ACCUMULATE across passes rather than a single-shot assert: the
            # product reap is BOUNDED and non-blocking BY DESIGN ("the next
            # tick continues the drain"), so one pass under scheduler load may
            # see fewer zombies than forked.  Poll until all three are drained.
            total = 0

            def _all_drained() -> bool:
                nonlocal total
                total += bs.reap_zombie_children()
                return total >= 3

            assert _poll_until(_all_drained), (
                f"reap_zombie_children drained only {total} of 3 zombies "
                f"within {_ZOMBIE_DEADLINE}s"
            )
            # all three are really gone: an explicit waitpid finds no child.
            for pid in pids:
                with pytest.raises(ChildProcessError):
                    _os.waitpid(pid, _os.WNOHANG)
        finally:
            for pid in pids:
                _reap_quietly(pid)

    def test_no_children_is_quiet_zero(self, monkeypatch):
        monkeypatch.setattr(
            bs.os, "waitpid",
            lambda *a: (_ for _ in ()).throw(ChildProcessError()),
        )
        assert bs.reap_zombie_children() == 0

    def test_oserror_is_swallowed(self, monkeypatch):
        def _boom(*a):
            raise OSError("waitpid exploded")
        monkeypatch.setattr(bs.os, "waitpid", _boom)
        assert bs.reap_zombie_children() == 0  # tolerant: no raise, no reaps

    def test_bounded_per_call(self, monkeypatch):
        """A zombie burst larger than the bound is drained across calls, never
        stalling one tick."""
        calls = {"n": 0}
        def _endless(*a):
            calls["n"] += 1
            return (10_000 + calls["n"], 0)
        monkeypatch.setattr(bs.os, "waitpid", _endless)
        assert bs.reap_zombie_children(max_reaps=5) == 5
        assert calls["n"] == 5

    def test_zombie_end_to_end_probe_unblinds_after_reap(self):
        """The bifrost wedge in miniature: zombie reads dead via the probe
        even BEFORE the reap, and after the reap the PID is gone entirely."""
        pid = _fork_zombie()
        try:
            # _fork_zombie guarantees state Z, so the probe verdict is
            # deterministic here (kill-0 sees it; stat reads Z → dead).
            assert bs._default_pid_alive(pid) is False  # probe end
            # Reap end: accumulate-and-poll like test_drains_multiple_zombies
            # (the product reap is bounded/non-blocking by design).
            total = 0

            def _reaped_one() -> bool:
                nonlocal total
                total += bs.reap_zombie_children()
                return total >= 1

            assert _poll_until(_reaped_one), (
                f"reap_zombie_children reaped nothing within {_ZOMBIE_DEADLINE}s "
                f"(zombie child {pid} outstanding)"
            )
            with pytest.raises(ChildProcessError):
                _os.waitpid(pid, _os.WNOHANG)
        finally:
            _reap_quietly(pid)


# --------------------------------------------------------------------------- #
# POST-BOOT XDG PROJECTION (project_pinned_xdg)                                #
# --------------------------------------------------------------------------- #
#
# The host half of the ruling pins the mount dests under ~/.kanibako/state because a
# mount dest must be concrete before the box is live. This half restores XDG
# compliance once there IS a box: $XDG_STATE_HOME/kanibako is pointed at the pinned
# dir. It is a SYMLINK, not the bind mount the design first reached for -- a box runs
# with an empty effective capability set, so an in-box `mount --bind` cannot execute.


class TestProjectPinnedXdg:
    def test_default_xdg_gets_a_symlink_to_the_pinned_dir(self, tmp_path):
        """With no XDG_STATE_HOME set, ~/.local/state/kanibako serves the pinned dir."""
        created = bs.project_pinned_xdg(home=tmp_path, environ={})
        link = tmp_path / ".local" / "state" / "kanibako"
        assert created == [str(link)]
        assert link.is_symlink()
        assert link.resolve() == (tmp_path / ".kanibako" / "state").resolve()

    def test_pinned_dir_is_created_so_the_link_is_never_dangling(self, tmp_path):
        bs.project_pinned_xdg(home=tmp_path, environ={})
        assert (tmp_path / ".kanibako" / "state").is_dir()

    def test_absolute_xdg_state_home_is_honored(self, tmp_path):
        """The projection follows the box's XDG setting -- that is its whole point:
        the HOST could not read it, PID-1 can."""
        elsewhere = tmp_path / "srv" / "state"
        created = bs.project_pinned_xdg(
            home=tmp_path, environ={"XDG_STATE_HOME": str(elsewhere)},
        )
        assert created == [str(elsewhere / "kanibako")]
        assert (elsewhere / "kanibako").resolve() == (
            tmp_path / ".kanibako" / "state"
        ).resolve()

    def test_relative_xdg_state_home_is_ignored_per_spec(self, tmp_path):
        """A relative value is invalid per the XDG spec -> the default is used."""
        created = bs.project_pinned_xdg(
            home=tmp_path, environ={"XDG_STATE_HOME": "relative/state"},
        )
        assert created == [str(tmp_path / ".local" / "state" / "kanibako")]

    def test_second_run_is_a_no_op(self, tmp_path):
        """PID-1 may re-run on a relaunch; a correct link is left alone, not
        recreated (and not reported as created)."""
        assert bs.project_pinned_xdg(home=tmp_path, environ={})
        assert bs.project_pinned_xdg(home=tmp_path, environ={}) == []

    def test_existing_real_directory_is_never_clobbered(self, tmp_path):
        """A box upgraded from a release that MOUNTED at ~/.local/state/kanibako has
        a real directory there. Deleting a user's directory is not PID-1's call, so
        the projection declines and the box comes up normally."""
        link = tmp_path / ".local" / "state" / "kanibako"
        link.mkdir(parents=True)
        (link / "leftover.jsonl").write_text("keep me\n")
        assert bs.project_pinned_xdg(home=tmp_path, environ={}) == []
        assert not link.is_symlink()
        assert (link / "leftover.jsonl").read_text() == "keep me\n"

    def test_foreign_symlink_is_not_repointed(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        link = tmp_path / ".local" / "state" / "kanibako"
        link.parent.mkdir(parents=True)
        link.symlink_to(other)
        assert bs.project_pinned_xdg(home=tmp_path, environ={}) == []
        assert link.resolve() == other.resolve()

    def test_same_path_is_skipped(self, tmp_path, monkeypatch):
        """The guard: nothing to project when the two resolve to one place.

        ⚑ It cannot fire for TODAY's row -- `$XDG_STATE_HOME/kanibako` can only
        equal `~/.kanibako/state` if the facet dir is named `kanibako`, and it is
        named `state`. So the row is substituted here to exercise the guard for
        real and to record the one shape that reaches it: a future facet named
        `kanibako` with the box pointing that XDG var at the pinned root. The guard
        is kept rather than deleted precisely because the table is meant to grow.
        """
        monkeypatch.setattr(
            bs, "XDG_PROJECTIONS", (("XDG_STATE_HOME", ".local/state", "kanibako"),),
        )
        created = bs.project_pinned_xdg(
            home=tmp_path, environ={"XDG_STATE_HOME": str(tmp_path / ".kanibako")},
        )
        assert created == []
        # And nothing was made on the way to deciding that.
        assert not (tmp_path / ".kanibako").exists()

    def test_unwritable_home_logs_and_returns_rather_than_raising(self, tmp_path):
        """PID-1 must never die of this: the pinned path is the REAL location and
        every in-box kanibako reader spells it directly, so a failed projection
        costs a convenience, not the box."""
        home = tmp_path / "home"
        home.mkdir()
        home.chmod(0o500)
        try:
            assert bs.project_pinned_xdg(home=home, environ={}) == []
        finally:
            home.chmod(0o700)

    def test_projection_table_has_one_row_and_names_state(self):
        """The table shape is the point: a second facet is a ROW, not a mechanism."""
        assert bs.XDG_PROJECTIONS == (("XDG_STATE_HOME", ".local/state", "state"),)


# --------------------------------------------------------------------------- #
# THE SHELL TWIN (xdg_projection_sh)                                          #
# --------------------------------------------------------------------------- #
#
# The Python half above runs ONLY where the supervisor is PID-1. A bare keep-alive
# box, the forward-compat fallback (which fires BECAUSE the import failed) and a
# helper box run no kanibako Python at PID-1 at all. So the same table also emits
# shell, and these tests hold the two halves to the SAME behaviour by running each
# against a real temporary HOME and comparing the resulting tree -- not by comparing
# their source text, which would pass while the shell did nothing.


def _run_projection_sh(home, env_extra=None, *, strict=True):
    """Execute the generated snippet against *home*; return the CompletedProcess.

    ``set -eu`` by default because helper-init.sh runs under ``set -euo pipefail`` --
    a snippet that tripped either option would abort a helper's entrypoint before it
    ever registered with the hub, which is exactly the class of new failure this
    projection must not introduce.
    """
    env = {"HOME": str(home), "PATH": _os.environ.get("PATH", "/usr/bin:/bin")}
    env.update(env_extra or {})
    script = bs.xdg_projection_sh()
    if strict:
        script = "set -eu\n" + script
    return subprocess.run(
        ["sh", "-c", script], env=env, capture_output=True, text=True, check=False,
    )


class TestXdgProjectionSh:
    def test_default_xdg_gets_the_same_symlink_the_python_half_makes(self, tmp_path):
        proc = _run_projection_sh(tmp_path)
        assert proc.returncode == 0, proc.stderr
        link = tmp_path / ".local" / "state" / "kanibako"
        assert link.is_symlink()
        assert link.resolve() == (tmp_path / ".kanibako" / "state").resolve()

    def test_pinned_dir_is_created_so_the_link_is_never_dangling(self, tmp_path):
        _run_projection_sh(tmp_path)
        assert (tmp_path / ".kanibako" / "state").is_dir()

    def test_absolute_xdg_state_home_is_honored(self, tmp_path):
        elsewhere = tmp_path / "srv" / "state"
        proc = _run_projection_sh(
            tmp_path, {"XDG_STATE_HOME": str(elsewhere)},
        )
        assert proc.returncode == 0, proc.stderr
        assert (elsewhere / "kanibako").resolve() == (
            tmp_path / ".kanibako" / "state"
        ).resolve()
        assert not (tmp_path / ".local" / "state" / "kanibako").exists()

    def test_relative_xdg_state_home_is_ignored_per_spec(self, tmp_path):
        _run_projection_sh(tmp_path, {"XDG_STATE_HOME": "relative/state"})
        assert (tmp_path / ".local" / "state" / "kanibako").is_symlink()

    def test_second_run_is_a_no_op(self, tmp_path):
        _run_projection_sh(tmp_path)
        link = tmp_path / ".local" / "state" / "kanibako"
        before = _os.readlink(link)
        proc = _run_projection_sh(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert _os.readlink(link) == before

    def test_existing_real_directory_is_never_clobbered(self, tmp_path):
        """The MIGRATION.md §2.22 shape: a pre-v1.8.0 box has a REAL directory
        there, holding real files. Removing it is the user's call, never ours."""
        link = tmp_path / ".local" / "state" / "kanibako"
        link.mkdir(parents=True)
        (link / "leftover.jsonl").write_text("keep me\n")
        proc = _run_projection_sh(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert not link.is_symlink()
        assert (link / "leftover.jsonl").read_text() == "keep me\n"

    def test_a_refused_link_still_leaves_the_pinned_dir_made(self, tmp_path):
        """Parity with the Python half, whose `pinned.mkdir` runs BEFORE the two
        refusals -- so a box that declines the link still has the real location. The
        shell nests its guards rather than flattening them for exactly this."""
        link = tmp_path / ".local" / "state" / "kanibako"
        link.mkdir(parents=True)
        _run_projection_sh(tmp_path)
        assert (tmp_path / ".kanibako" / "state").is_dir()
        assert not link.is_symlink()

    def test_foreign_symlink_is_not_repointed(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        link = tmp_path / ".local" / "state" / "kanibako"
        link.parent.mkdir(parents=True)
        link.symlink_to(other)
        _run_projection_sh(tmp_path)
        assert link.resolve() == other.resolve()

    def test_dangling_symlink_is_left_alone_too(self, tmp_path):
        """`-L` is tested BEFORE `-e` precisely for this: `-e` is false for a
        dangling link, so an `-e`-only guard would treat the slot as empty."""
        link = tmp_path / ".local" / "state" / "kanibako"
        link.parent.mkdir(parents=True)
        link.symlink_to(tmp_path / "gone")
        proc = _run_projection_sh(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert _os.readlink(link) == str(tmp_path / "gone")

    def test_same_path_is_skipped_and_nothing_is_created(self, tmp_path, monkeypatch):
        """The Python half's guard, in shell: substitute a facet named `kanibako`
        (see TestProjectPinnedXdg.test_same_path_is_skipped for why the shipped row
        cannot reach it) and point the XDG var at the pinned root."""
        monkeypatch.setattr(
            bs, "XDG_PROJECTIONS", (("XDG_STATE_HOME", ".local/state", "kanibako"),),
        )
        proc = _run_projection_sh(
            tmp_path, {"XDG_STATE_HOME": str(tmp_path / ".kanibako")},
        )
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / ".kanibako").exists()

    def test_unwritable_home_exits_zero_rather_than_failing(self, tmp_path):
        """The forward-compat contract: this snippet is composed AHEAD of the
        `probe && exec supervisor || exec fallback` chain, so a non-zero exit here
        would not merely skip a convenience -- it would pick a different PID-1."""
        home = tmp_path / "home"
        home.mkdir()
        home.chmod(0o500)
        try:
            proc = _run_projection_sh(home)
            assert proc.returncode == 0, proc.stderr
            assert not (home / ".local").exists()
            assert not (home / ".kanibako").exists()
        finally:
            home.chmod(0o700)

    def test_it_is_generated_from_the_table_not_hand_written(self, monkeypatch):
        """A second facet must extend the SHELL as it extends the Python -- that is
        the whole claim of the table's `never a second mechanism` comment."""
        monkeypatch.setattr(
            bs,
            "XDG_PROJECTIONS",
            (
                ("XDG_STATE_HOME", ".local/state", "state"),
                ("XDG_CACHE_HOME", ".cache", "cache"),
            ),
        )
        script = bs.xdg_projection_sh()
        assert '_kb_pin="$HOME/.kanibako/cache"' in script
        assert '_kb_xdg="${XDG_CACHE_HOME:-}"' in script
        assert script.count("_kb_link=") == 2

    @pytest.mark.parametrize(
        "scenario",
        ["virgin", "already_projected", "real_dir_present", "foreign_symlink",
         "dangling_symlink", "absolute_xdg", "relative_xdg"],
    )
    def test_the_two_halves_produce_an_IDENTICAL_tree(self, tmp_path, scenario):
        """The docstring's claim -- `interchangeable and idempotent against each
        other` -- asserted directly rather than inferred from matching prose.

        Each half runs against its OWN fresh home prepared identically, and the
        resulting trees (paths, kinds, and symlink TARGETS rewritten home-relative)
        must match exactly. This is the test that fails if the generator drifts off
        the Python in a way both halves' own tests happen to tolerate.
        """
        env = {}
        if scenario == "absolute_xdg":
            env = {"XDG_STATE_HOME": "$HOME/srv/state"}
        elif scenario == "relative_xdg":
            env = {"XDG_STATE_HOME": "relative/state"}

        def prepare(home):
            link = home / ".local" / "state" / "kanibako"
            if scenario == "already_projected":
                link.parent.mkdir(parents=True)
                link.symlink_to(home / ".kanibako" / "state")
            elif scenario == "real_dir_present":
                link.mkdir(parents=True)
                (link / "leftover.jsonl").write_text("keep me\n")
            elif scenario == "foreign_symlink":
                (home / "other").mkdir(parents=True)
                link.parent.mkdir(parents=True)
                link.symlink_to(home / "other")
            elif scenario == "dangling_symlink":
                link.parent.mkdir(parents=True)
                link.symlink_to(home / "gone")

        def tree(home):
            out = []
            for p in sorted(home.rglob("*")):
                rel = p.relative_to(home).as_posix()
                if p.is_symlink():
                    target = _os.readlink(p).replace(str(home), "$HOME")
                    out.append(f"link {rel} -> {target}")
                else:
                    out.append(f"{'dir ' if p.is_dir() else 'file'} {rel}")
            return out

        py_home = tmp_path / "py"
        sh_home = tmp_path / "sh"
        for home in (py_home, sh_home):
            home.mkdir()
            prepare(home)

        py_env = {k: v.replace("$HOME", str(py_home)) for k, v in env.items()}
        bs.project_pinned_xdg(home=py_home, environ=py_env)
        _run_projection_sh(
            sh_home, {k: v.replace("$HOME", str(sh_home)) for k, v in env.items()},
        )
        assert tree(sh_home) == tree(py_home)

    def test_helper_init_carries_the_snippet_verbatim(self):
        """helper-init.sh is bash and can import nothing, so it holds a COPY -- the
        same arrangement as its SOCKET_PATH literal. This is the pin that keeps the
        copy from drifting off the generator."""
        script = (
            _Path(bs.__file__).parent / "scripts" / "helper-init.sh"
        ).read_text()
        assert bs.xdg_projection_sh() in script


# --------------------------------------------------------------------------- #
# DIRECTIVE FRESHNESS -- the flattened instruction slot, kept true for the box's  #
# whole life instead of only at launch.                                          #
# --------------------------------------------------------------------------- #
#
# The launch shim flattens the directive chain into the agent's native slot ONCE per
# agent launch and swallows every failure (`|| true`), so a directive edited
# mid-container-life leaves the authoritative file stale and says nothing.  These pin
# the watch that fixes it: a RECEIPT of what the render read, re-hashed every few
# ticks, with a re-flatten on any move.

_FLATTENER = str(
    _resources.files("kanibako.scripts").joinpath("import-directives.py")
)


def _sha(text: str) -> str:
    return _hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest(**over: object) -> dict:
    base: dict[str, object] = {
        "version": DIRECTIVE_MANIFEST_VERSION,
        "seed": "/seed.md",
        "dest": "/dest.md",
        "output_sha256": _sha("rendered"),
        "inputs": [{"path": "/seed.md", "sha256": _sha("body")}],
    }
    base.update(over)
    return base


def _probe(**over: str | None) -> dict[str, str | None]:
    probe: dict[str, str | None] = {
        "/seed.md": _sha("body"),
        "/dest.md": _sha("rendered"),
    }
    probe.update(over)
    return probe


# --- the pure verdict ------------------------------------------------------

def test_directives_fresh_when_every_input_and_the_output_match():
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", _probe()) is (
        DirectiveVerdict.FRESH
    )


def test_directives_changed_input_is_stale():
    probe = _probe(**{"/seed.md": _sha("EDITED")})
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", probe) is (
        DirectiveVerdict.STALE
    )


def test_directives_absent_input_that_now_exists_is_stale():
    """🛑 THE MISS SIDE. Nothing the hit side watches moved -- a hits-only check would
    never fire, and the file a user just created would never reach the agent."""
    man = _manifest(inputs=[
        {"path": "/seed.md", "sha256": _sha("body")},
        {"path": "/late.md", "absent": True},
    ])
    probe = _probe(**{"/late.md": _sha("i exist now")})
    assert decide_directives(man, "/seed.md", "/dest.md", probe) is DirectiveVerdict.STALE


def test_directives_absent_input_still_absent_is_fresh():
    # ...and it must NOT re-fire every tick, which is why "absent" means "yields no
    # content" (a directory, or a still-unreadable file, keeps yielding none).
    man = _manifest(inputs=[
        {"path": "/seed.md", "sha256": _sha("body")},
        {"path": "/late.md", "absent": True},
    ])
    probe = _probe(**{"/late.md": None})
    assert decide_directives(man, "/seed.md", "/dest.md", probe) is DirectiveVerdict.FRESH


def test_directives_input_that_can_no_longer_be_read_is_stale():
    probe = _probe(**{"/seed.md": None})
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", probe) is (
        DirectiveVerdict.STALE
    )


def test_directives_hand_edited_output_is_not_stale():
    """Inputs untouched but the generated file changed => a human edited it. Rewriting
    it is the seed-clobber class of data loss; refreshing it on the next real source
    change is not."""
    probe = _probe(**{"/dest.md": _sha("hand written")})
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", probe) is (
        DirectiveVerdict.HAND_EDITED
    )


def test_directives_changed_input_beats_a_hand_edit():
    # Both moved: the sources win -- "refresh on the first" is the ruled policy.
    probe = _probe(**{"/seed.md": _sha("EDITED"), "/dest.md": _sha("hand written")})
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", probe) is (
        DirectiveVerdict.STALE
    )


def test_directives_missing_output_is_stale_not_hand_edited():
    # DEST beats the receipt: if the artifact is gone, no receipt makes it present.
    probe = _probe(**{"/dest.md": None})
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", probe) is (
        DirectiveVerdict.STALE
    )


@pytest.mark.parametrize("manifest", [
    None,                                          # absent
    "not json at all",                             # unparseable
    {},                                            # no version
    {"version": 999, "inputs": []},                # unknown version
    {"version": DIRECTIVE_MANIFEST_VERSION, "seed": "/seed.md",
     "dest": "/dest.md", "inputs": "not-a-list"},  # malformed inputs
    {"version": DIRECTIVE_MANIFEST_VERSION, "seed": "/seed.md",
     "dest": "/dest.md", "inputs": [{"sha256": "x"}]},   # an entry with no path
])
def test_directives_every_unusable_receipt_means_reflatten(manifest):
    """The property that makes this safe inside PID 1: the worst outcome of ANY
    confusion is one unnecessary render. There is no state in which the watcher can do
    something worse than that."""
    assert decide_directives(manifest, "/seed.md", "/dest.md", _probe()) is (
        DirectiveVerdict.STALE
    )


def test_directives_unknown_version_is_stale_even_when_all_else_matches():
    """The version gate has to be pinned on an OTHERWISE-VALID receipt, or it looks
    covered by the malformed cases while doing nothing. Never parse an unknown shape
    defensively into a wrong answer: format evolution is then free, and the cost of
    refusing to guess is one render."""
    man = _manifest(version=DIRECTIVE_MANIFEST_VERSION + 1)
    assert decide_directives(man, "/seed.md", "/dest.md", _probe()) is (
        DirectiveVerdict.STALE
    )
    # ...and the SAME receipt at the known version is fresh, so the version is the
    # only thing this asserts.
    assert decide_directives(_manifest(), "/seed.md", "/dest.md", _probe()) is (
        DirectiveVerdict.FRESH
    )


@pytest.mark.parametrize("seed,dest", [("/other.md", "/dest.md"), ("/seed.md", "/other")])
def test_directives_receipt_for_a_different_job_is_stale(seed, dest):
    probe = _probe(**{"/other": _sha("rendered"), "/other.md": _sha("body")})
    assert decide_directives(_manifest(), seed, dest, probe) is DirectiveVerdict.STALE


# --- the check, against real files -----------------------------------------

class _FlattenRun(FakeRun):
    """FakeRun that also accepts the (non-tmux) flattener call.

    ``run`` is the module's ONE subprocess seam; the base fake asserts every call is
    tmux, so the flatten is recorded here instead of loosening that guard.  With
    ``execute=True`` the REAL flattener runs, so a loop test proves the whole route --
    argv included -- rather than a fake's idea of it.
    """

    def __init__(self, *, execute: bool = False, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.flattens: list[list[str]] = []
        self._execute = execute

    def __call__(self, args, **kwargs):
        if args and args[0] != "tmux":
            self.flattens.append(list(args))
            if self._execute:
                return subprocess.run(args, **kwargs)
            return subprocess.CompletedProcess(args, 0, "", "")
        return super().__call__(args, **kwargs)


@pytest.fixture
def directive_box(tmp_path):
    """A box whose directive chain is really flattened on disk, with its receipt."""
    seed = tmp_path / "kickoff.md"
    seed.write_text("kickoff @canon.md and @later.md\n", encoding="utf-8")
    (tmp_path / "canon.md").write_text("canon body\n", encoding="utf-8")
    dest = tmp_path / "CLAUDE.md"
    manifest = tmp_path / ".kanibako" / "directive-manifest.json"
    watch = DirectiveWatch(
        seed=str(seed), dest=str(dest), manifest=str(manifest), flattener=_FLATTENER,
    )
    rc = subprocess.run(
        [_sys.executable, _FLATTENER, str(seed), str(dest),
         "--manifest", str(manifest)],
        capture_output=True, text=True,
    ).returncode
    assert rc == 0
    return watch, tmp_path


def _supervisor(watch, **over):
    fake = _FlattenRun(execute=True)
    sup = BoxSupervisor(_config(directives=watch, **over), run=fake, proc_cmdlines=[])
    return sup, fake


def test_check_directives_is_none_when_the_launcher_threaded_no_watch():
    sup = BoxSupervisor(_config(), run=FakeRun())
    assert sup.check_directives() is None


def test_check_directives_fresh_right_after_the_launch_flatten(directive_box):
    watch, _tmp = directive_box
    sup, fake = _supervisor(watch)
    assert sup.check_directives() is DirectiveVerdict.FRESH
    assert fake.flattens == []          # an unchanged tick writes NOTHING


def test_check_directives_reflattens_after_a_source_edit(directive_box):
    watch, tmp = directive_box
    sup, fake = _supervisor(watch)
    (tmp / "canon.md").write_text("canon body, REVISED\n", encoding="utf-8")

    assert sup.check_directives() is DirectiveVerdict.STALE
    assert "REVISED" in _Path(watch.dest).read_text()
    # EXACTLY ONE rewrite: the receipt is refreshed by the same run, so the next
    # tick is fresh again and the box does not re-render every 5 seconds forever.
    assert len(fake.flattens) == 1
    assert sup.check_directives() is DirectiveVerdict.FRESH
    assert len(fake.flattens) == 1


def test_check_directives_reflattens_when_a_missing_import_appears(directive_box):
    """The case a hits-only watcher cannot see: nothing it was watching changed."""
    watch, tmp = directive_box
    sup, fake = _supervisor(watch)
    assert "later body" not in _Path(watch.dest).read_text()

    (tmp / "later.md").write_text("later body\n", encoding="utf-8")

    assert sup.check_directives() is DirectiveVerdict.STALE
    assert "later body" in _Path(watch.dest).read_text()
    assert sup.check_directives() is DirectiveVerdict.FRESH


def test_check_directives_leaves_a_hand_edited_slot_alone(directive_box):
    watch, _tmp = directive_box
    sup, fake = _supervisor(watch)
    _Path(watch.dest).write_text("MY OWN NOTES\n", encoding="utf-8")

    assert sup.check_directives() is DirectiveVerdict.HAND_EDITED
    assert _Path(watch.dest).read_text() == "MY OWN NOTES\n"
    assert fake.flattens == []


def test_hand_edit_is_announced_once_not_every_tick(directive_box, caplog):
    watch, _tmp = directive_box
    sup, _fake = _supervisor(watch)
    _Path(watch.dest).write_text("MY OWN NOTES\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        for _ in range(4):
            sup.check_directives()
    assert sum("hand-edited" in r.message for r in caplog.records) == 1


def test_a_source_change_still_refreshes_a_hand_edited_slot(directive_box):
    watch, tmp = directive_box
    sup, _fake = _supervisor(watch)
    _Path(watch.dest).write_text("MY OWN NOTES\n", encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.HAND_EDITED

    (tmp / "canon.md").write_text("canon body, REVISED\n", encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.STALE
    assert "REVISED" in _Path(watch.dest).read_text()


@pytest.mark.parametrize("body", ["", "{ not json", '{"version": 999}'])
def test_an_unusable_receipt_on_disk_reflattens(directive_box, body):
    watch, _tmp = directive_box
    sup, fake = _supervisor(watch)
    _Path(watch.manifest).write_text(body, encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.STALE
    assert len(fake.flattens) == 1
    # and the run restored a usable receipt, so the next tick settles
    assert sup.check_directives() is DirectiveVerdict.FRESH


def test_a_receipt_from_a_newer_flattener_reflattens(directive_box):
    """A real, complete receipt whose only defect is a version this build does not
    know: still stale. The skew between the flattener's ``MANIFEST_VERSION`` and this
    module's ``DIRECTIVE_MANIFEST_VERSION`` is made SAFE, not prevented."""
    watch, _tmp = directive_box
    sup, fake = _supervisor(watch)
    receipt = _json.loads(_Path(watch.manifest).read_text())
    receipt["version"] = DIRECTIVE_MANIFEST_VERSION + 1
    _Path(watch.manifest).write_text(_json.dumps(receipt))

    assert sup.check_directives() is DirectiveVerdict.STALE
    assert len(fake.flattens) == 1
    assert sup.check_directives() is DirectiveVerdict.FRESH


def test_a_deleted_receipt_reflattens(directive_box):
    watch, _tmp = directive_box
    sup, fake = _supervisor(watch)
    _Path(watch.manifest).unlink()
    assert sup.check_directives() is DirectiveVerdict.STALE
    assert _Path(watch.manifest).is_file()


def test_a_deleted_slot_is_rebuilt(directive_box):
    watch, _tmp = directive_box
    sup, _fake = _supervisor(watch)
    _Path(watch.dest).unlink()
    assert sup.check_directives() is DirectiveVerdict.STALE
    assert "canon body" in _Path(watch.dest).read_text()


def test_reflatten_argv_is_the_launcher_paths_in_file_mode(directive_box):
    """The supervisor spells NO path of its own: seed, dest, manifest and the
    flattener all arrive from the launcher (the no-third-carrier rule)."""
    watch, tmp = directive_box
    fake = _FlattenRun(execute=False)
    sup = BoxSupervisor(_config(directives=watch), run=fake, proc_cmdlines=[])
    (tmp / "canon.md").write_text("edited\n", encoding="utf-8")
    sup.check_directives()
    assert fake.flattens == [[
        _sys.executable, _FLATTENER, watch.seed, watch.dest,
        "--manifest", watch.manifest,
    ]]
    assert "--additional-context" not in fake.flattens[0]


def test_a_failing_flatten_does_not_raise(directive_box):
    watch, tmp = directive_box
    fake = _FlattenRun(execute=False, rc={})
    sup = BoxSupervisor(_config(directives=watch), run=fake, proc_cmdlines=[])

    def boom(args, **kw):
        fake.flattens.append(list(args))
        return subprocess.CompletedProcess(args, 2, "", "import-directives: nope")

    sup._run = boom  # type: ignore[method-assign]
    (tmp / "canon.md").write_text("edited\n", encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.STALE   # no exception escapes


def test_a_flattener_that_cannot_be_launched_does_not_raise(directive_box):
    watch, tmp = directive_box
    sup = BoxSupervisor(_config(directives=watch), run=FakeRun(), proc_cmdlines=[])

    def missing(args, **kw):
        raise FileNotFoundError("no python3")

    sup._run = missing  # type: ignore[method-assign]
    (tmp / "canon.md").write_text("edited\n", encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.STALE


def test_a_hanging_flatten_is_bounded_and_does_not_raise(directive_box):
    """PID 1 calls the flattener SYNCHRONOUSLY inside its tick, and the sources live on
    an NFS home — an unbounded read would wedge the reap, the detach hook and self-heal
    behind it. The call carries a timeout, and the timeout is handled, not propagated."""
    watch, tmp = directive_box
    sup = BoxSupervisor(_config(directives=watch), run=FakeRun(), proc_cmdlines=[])
    seen: list[float | None] = []

    def hang(args, **kw):
        seen.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(args, kw.get("timeout") or 0)

    sup._run = hang  # type: ignore[method-assign]
    (tmp / "canon.md").write_text("edited\n", encoding="utf-8")
    assert sup.check_directives() is DirectiveVerdict.STALE
    assert seen == [bs.FLATTEN_TIMEOUT]


def test_safe_check_swallows_a_raising_check():
    sup = BoxSupervisor(_config(), run=FakeRun())

    def boom() -> DirectiveVerdict:
        raise RuntimeError("probe blew up")

    sup.check_directives = boom  # type: ignore[method-assign]
    sup._safe_check_directives()  # PID 1 must not die on a hiccup


# --- the loop (driven by the INJECTED sleeper; nothing waits on a clock) ----

def test_run_forever_checks_directives_on_the_cadence_not_every_tick(directive_box):
    watch, tmp = directive_box
    checks: list[int] = []
    # 5s target against a 2s tick => every 2nd tick.
    sup = BoxSupervisor(
        _config(directives=watch), run=FakeRun(rc={"has-session": 0}), proc_cmdlines=[],
    )
    sup.check_directives = lambda: checks.append(1)  # type: ignore[method-assign]
    _stop_after(sup, 5)
    assert sup.run_forever() == 0
    assert len(checks) == 2          # ticks 2 and 4 of 5


def test_run_forever_reflattens_a_changed_source_exactly_once(directive_box):
    """End to end through the real loop and the real flattener: one edit, one rewrite,
    and every later tick silent."""
    watch, tmp = directive_box
    fake = _FlattenRun(execute=True, rc={"has-session": 0})
    sup = BoxSupervisor(
        _config(directives=DirectiveWatch(
            seed=watch.seed, dest=watch.dest, manifest=watch.manifest,
            flattener=watch.flattener, interval=2.0,   # every tick
        )),
        run=fake, proc_cmdlines=[],
    )
    (tmp / "canon.md").write_text("canon body, REVISED\n", encoding="utf-8")
    _stop_after(sup, 4)
    assert sup.run_forever() == 0
    assert len(fake.flattens) == 1
    assert "REVISED" in _Path(watch.dest).read_text()


def test_run_forever_does_not_touch_an_unchanged_slot(directive_box):
    watch, _tmp = directive_box
    fake = _FlattenRun(execute=True, rc={"has-session": 0})
    sup = BoxSupervisor(
        _config(directives=DirectiveWatch(
            seed=watch.seed, dest=watch.dest, manifest=watch.manifest,
            flattener=watch.flattener, interval=2.0,
        )),
        run=fake, proc_cmdlines=[],
    )
    before = _Path(watch.dest).stat().st_ino
    _stop_after(sup, 4)
    assert sup.run_forever() == 0
    assert fake.flattens == []
    assert _Path(watch.dest).stat().st_ino == before


def test_run_forever_is_byte_unchanged_without_a_watch():
    # An old launcher (or a no-agent box) threads no watch: nothing extra runs.
    fake = _FlattenRun(execute=False, rc={"has-session": 0})
    sup = BoxSupervisor(_config(), run=fake, proc_cmdlines=[])
    _stop_after(sup, 3)
    assert sup.run_forever() == 0
    assert fake.flattens == []


def test_panel_watch_also_keeps_the_slot_fresh(directive_box):
    """A warm-only box has directives and a panel agent reading them; the slot must not
    go stale just because this box was fronted by the panel instead of a CLI."""
    watch, tmp = directive_box
    fake = _FlattenRun(execute=True, rc={"has-session": 0})
    sup = BoxSupervisor(
        _config(panel_watch=True, directives=DirectiveWatch(
            seed=watch.seed, dest=watch.dest, manifest=watch.manifest,
            flattener=watch.flattener, interval=2.0,
        )),
        run=fake, proc_cmdlines=[],
    )
    (tmp / "canon.md").write_text("canon body, REVISED\n", encoding="utf-8")
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert "REVISED" in _Path(watch.dest).read_text()


# --- argv wiring -----------------------------------------------------------

def test_config_from_argv_threads_the_four_directive_paths():
    cfg = config_from_argv([
        "--session", "s", "--marker", "m",
        "--directive-seed", "/home/agent/.config/kanibako/kickoff.md",
        "--directive-dest", "/home/agent/.claude/CLAUDE.md",
        "--directive-manifest", "/home/agent/.kanibako/directive-manifest.json",
        "--directive-flattener", "/opt/kanibako/kanibako/scripts/import-directives.py",
        "--", "claude",
    ])
    assert cfg.directives == DirectiveWatch(
        seed="/home/agent/.config/kanibako/kickoff.md",
        dest="/home/agent/.claude/CLAUDE.md",
        manifest="/home/agent/.kanibako/directive-manifest.json",
        flattener="/opt/kanibako/kanibako/scripts/import-directives.py",
    )


def test_config_from_argv_defaults_to_no_directive_watch():
    cfg = config_from_argv(["--session", "s", "--marker", "m", "--", "claude"])
    assert cfg.directives is None


def test_config_from_argv_refuses_to_half_arm_the_directive_watch(caplog):
    """A partial set is a launcher bug. Erroring would refuse to start a box over a
    watch that is not the box's purpose; guessing the missing path would put a path
    decision back in the supervisor. So: say so, stay inert."""
    with caplog.at_level("WARNING"):
        cfg = config_from_argv([
            "--session", "s", "--marker", "m",
            "--directive-manifest", "/home/agent/.kanibako/directive-manifest.json",
            "--", "claude",
        ])
    assert cfg.directives is None
    assert any("directive freshness NOT armed" in r.message for r in caplog.records)


def test_directive_tick_period_converts_seconds_to_whole_ticks():
    watch = DirectiveWatch(seed="s", dest="d", manifest="m", flattener="f", interval=5.0)
    sup = BoxSupervisor(_config(directives=watch, poll_interval=2.0), run=FakeRun())
    assert sup._directive_tick_period() == 2
    sup = BoxSupervisor(_config(directives=watch, poll_interval=60.0), run=FakeRun())
    assert sup._directive_tick_period() == 1     # never zero -- that would never fire
