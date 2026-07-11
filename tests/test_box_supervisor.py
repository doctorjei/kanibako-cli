"""Tests for kanibako.box_supervisor — the PID-1 always-on-agent supervisor.

Increment 2 (E2a) is the supervisor MODULE only.  The PURE decision function
(:func:`decide`) is exercised exhaustively; the impure tmux actions are driven
through an injected fake ``run`` (asserting the exact tmux argv), and the loop /
self-heal / backoff run instantly via an injected ``sleep`` — no real tmux, no
real agent, no real waiting.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

from kanibako.box_lifecycle import AttachState
from kanibako.box_supervisor import (
    ActionKind,
    BoxSupervisor,
    PanelActionKind,
    PanelAgentState,
    SupervisorConfig,
    config_from_argv,
    decide,
    decide_panel,
    main,
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

def test_run_forever_teardown_policy_closes_box_on_clean_agent_exit():
    # Foreground CLI 'teardown' policy: agent alive at startup, then a CLEAN dead
    # pane (pane_dead_status=0) on tick1 → the loop RETURNS 0 (box closes) WITHOUT
    # any self-heal restart (no self-heal loop while a human-driven CLI is the
    # surface).  E2d: the return code is the AGENT's real code, 0 for a clean exit.
    fake = FakeRun(
        rc={"has-session": [0, 0]},
        # startup liveness="" (alive); tick1 liveness="0" (dead, clean); teardown
        # re-read="0".
        stdout={"display-message": ["", "0", "0"]},
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    restarts: list[int] = []
    sup.restart_agent_session = (  # type: ignore[method-assign]
        lambda: restarts.append(1) or True
    )
    assert sup.run_forever() == 0
    assert restarts == []                       # never self-healed
    assert fake.sub_calls("new-session") == []  # and never restarted the agent


def test_run_forever_teardown_returns_agent_dead_status():
    # E2d: under 'teardown', a DEAD pane carrying pane_dead_status=42 (the agent
    # crashed) makes run_forever return 42 — the agent's TRUE exit code — so the
    # container's exit code (and the host's error handling) is truthful, not a lie.
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


def test_run_forever_teardown_echoes_agent_output_to_stdout(capsys):
    # On teardown the supervisor is PID-1, so the crashed agent's output (living in
    # its tmux pane) never reaches podman logs unless the supervisor echoes it.  The
    # host reads podman logs to (a) show the user WHY the agent died and (b) detect a
    # recoverable "no conversation" retry — so the captured pane MUST be printed to
    # stdout before the box closes.
    fake = FakeRun(
        rc={"has-session": [0, 0]},
        stdout={
            "display-message": ["", "1", "1"],
            "capture-pane": "No conversation found to continue\n",
        },
    )
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 1
    assert "No conversation found to continue" in capsys.readouterr().out


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


def test_run_forever_teardown_returns_one_when_dead_status_unknown():
    # E2d: under 'teardown', the session VANISHES entirely (has-session != 0), so no
    # pane_dead_status is readable → a dead-but-unknown agent is a FAILURE, not a
    # success: run_forever defaults to rc 1.
    fake = FakeRun(rc={"has-session": [0, 1]})
    sup = BoxSupervisor(_config(on_agent_exit="teardown"), run=fake, proc_cmdlines=[])
    assert sup.run_forever() == 1
    assert fake.sub_calls("new-session") == []  # never restarted


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


def test_run_forever_teardown_initial_start_failure_returns_and_closes():
    # Foreground 'teardown': the INITIAL agent start fails (new-session rc!=0) → the
    # loop returns NON-ZERO so the box closes and the host surfaces the start error,
    # rather than self-healing (design §86-88 cold-start-error-human-direct).
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


# -- panel_agent_state (the tolerant, injectable marker probe) ---------------

def _panel_sup(
    *,
    pidfile: str | None = "/run/kanibako/agent.pid",
    contents: str | None = "4242\n",
    alive=lambda pid: True,
    raise_read: bool = False,
    raise_alive: bool = False,
) -> BoxSupervisor:
    def read(path: str) -> str | None:
        if raise_read:
            raise OSError("pidfile read boom")
        return contents

    def pid_alive(pid: int) -> bool:
        if raise_alive:
            raise OSError("kill(0) boom")
        return alive(pid)

    return BoxSupervisor(
        _config(agent_pidfile=pidfile),
        run=FakeRun(),
        read_pidfile=read,
        pid_alive=pid_alive,
    )


def test_panel_agent_state_none_when_no_pidfile_configured():
    assert _panel_sup(pidfile=None).panel_agent_state() is PanelAgentState.NONE


def test_panel_agent_state_none_when_absent():
    assert _panel_sup(contents=None).panel_agent_state() is PanelAgentState.NONE


@pytest.mark.parametrize("contents", ["", "   \n", "notapid", "12x", "-1", "0"])
def test_panel_agent_state_none_on_empty_or_garbage(contents):
    assert _panel_sup(contents=contents).panel_agent_state() is PanelAgentState.NONE


def test_panel_agent_state_alive_for_live_pid():
    assert _panel_sup(contents="777", alive=lambda pid: True).panel_agent_state() is (
        PanelAgentState.ALIVE
    )


def test_panel_agent_state_dead_for_stale_pid():
    assert _panel_sup(contents="777", alive=lambda pid: False).panel_agent_state() is (
        PanelAgentState.DEAD
    )


def test_panel_agent_state_tolerates_a_raising_reader_and_probe():
    # A raising file read OR liveness probe must never propagate (PID-1 immortality):
    # both degrade to NONE ("no live panel agent").
    assert _panel_sup(raise_read=True).panel_agent_state() is PanelAgentState.NONE
    assert _panel_sup(raise_alive=True).panel_agent_state() is PanelAgentState.NONE


# -- the panel-watch loop (agent-independent `code` warm-up) ------------------

def _panel_watch_sup(fake: FakeRun, *, contents=None, alive=lambda pid: True) -> BoxSupervisor:
    """A panel-watch supervisor with an injected marker reader/liveness probe."""
    return BoxSupervisor(
        _config(panel_watch=True, agent_pidfile="/run/kanibako/agent.pid"),
        run=fake,
        proc_cmdlines=[],
        read_pidfile=lambda _p: contents,
        pid_alive=alive,
    )


def test_panel_watch_startup_is_agentless_and_stays_up():
    # MUTATION-PROOF (the regression this arc closes): panel-watch startup starts NO
    # CLI agent.  A never-attached box (no surface, no marker) stays up through the
    # grace until the harness stops it — and NEVER emits a tmux new-session.
    fake = FakeRun(rc={"has-session": 1})  # no tmux agent present
    sup = _panel_watch_sup(fake, contents=None)  # panel marker absent → NONE
    _script_snapshots(sup, [_NONE])
    slept = _stop_after(sup, 3)
    assert sup.run_forever() == 0
    assert fake.sub_calls("new-session") == []   # never started an agent
    assert len(slept) == 3                        # stayed up (grace) to the harness bound


def test_panel_watch_dead_marker_with_server_self_heals_a_cli_agent():
    # The §89-96 fallback: the panel agent DIED (stale marker) with the panel still
    # connected (vscode_server) → self-heal a CLI agent.  Stub _self_heal to record.
    fake = FakeRun(rc={"has-session": 1})
    sup = _panel_watch_sup(fake, contents="4242", alive=lambda pid: False)  # DEAD marker
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
    sup = _panel_watch_sup(fake, contents="999", alive=lambda pid: True)  # ALIVE marker
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
    sup = _panel_watch_sup(fake, contents=None)  # panel marker absent → NONE
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
    sup = _panel_watch_sup(fake, contents="4242", alive=lambda pid: False)  # DEAD marker
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
    sup = _panel_watch_sup(fake, contents="999", alive=lambda pid: True)  # ALIVE → hands-off
    _script_snapshots(sup, [_BOTH, _VS])  # tmux terminal detaches, panel stays
    fired: list[int] = []
    sup._on_detach = lambda: fired.append(1)  # type: ignore[method-assign]
    _stop_after(sup, 2)
    assert sup.run_forever() == 0
    assert fired == [1]  # exactly one detach hook when the terminal surface dropped


# -- config_from_argv (panel-watch flags) ------------------------------------

def test_config_from_argv_panel_watch_allows_no_trailing_agent_argv():
    # Panel-watch starts NO agent, so an EMPTY start_argv is allowed (the "no agent
    # argv" error is suppressed); --continue-cmd carries the self-heal grammar.
    cfg = config_from_argv(
        ["--session", "kanibako", "--marker", _MARKER, "--panel-watch",
         "--agent-pidfile", "/run/kanibako/agent.pid",
         "--continue-cmd", "claude --continue"]
    )
    assert cfg.panel_watch is True
    assert cfg.agent_pidfile == "/run/kanibako/agent.pid"
    assert cfg.start_argv == []
    assert cfg.continue_argv == ["claude", "--continue"]


def test_config_from_argv_defaults_panel_watch_off_and_no_pidfile():
    cfg = config_from_argv(["--session", "s", "--marker", "m", "--", "claude"])
    assert cfg.panel_watch is False
    assert cfg.agent_pidfile is None
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
