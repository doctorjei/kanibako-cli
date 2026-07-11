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
    SupervisorConfig,
    config_from_argv,
    decide,
    main,
)

# --- attach-state fixtures (mirror box_lifecycle's surfaces) ----------------

_NONE = AttachState(vscode_server=False, tmux_terminal=False)
_TM = AttachState(vscode_server=False, tmux_terminal=True)
_VS = AttachState(vscode_server=True, tmux_terminal=False)
_BOTH = AttachState(vscode_server=True, tmux_terminal=True)

_MARKER = "[Agent handoff - Continue prior task(s)]"


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
    def _take(prog: object, sub: str) -> object:
        if isinstance(prog, list):
            if not prog:
                return None
            return prog[0] if len(prog) == 1 else prog.pop(0)
        return prog

    def __call__(self, args, **kwargs):
        assert args and args[0] == "tmux"
        sub = args[1] if len(args) > 1 else ""
        self.calls.append(list(args))
        if self._raise_on is not None and sub == self._raise_on:
            raise FileNotFoundError("tmux not found")
        rc = self._take(self._rc.get(sub, 0), sub)
        out = self._take(self._stdout.get(sub, ""), sub)
        return subprocess.CompletedProcess(args, returncode=int(rc), stdout=str(out), stderr="")

    def sub_calls(self, sub: str) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 1 and c[1] == sub]


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
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--",
         "claude", "--dangerously-skip-permissions"]
    ]


def test_start_agent_session_reports_failure_on_nonzero_rc():
    fake = FakeRun(rc={"new-session": 1})
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is False


def test_restart_agent_session_uses_continue_argv_and_sends_marker():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.restart_agent_session() is True
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue"]
    ]
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


def test_start_agent_session_arms_remain_on_exit():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is True
    # remain-on-exit is armed as a SEPARATE set-option after new-session, so the
    # dead-pane status survives the agent's exit (and exec in the secret shim).
    assert fake.sub_calls("set-option") == [
        ["tmux", "set-option", "-t", "kanibako", "remain-on-exit", "on"]
    ]


def test_start_agent_session_skips_remain_on_exit_when_new_session_fails():
    fake = FakeRun(rc={"new-session": 1})
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.start_agent_session() is False
    assert fake.sub_calls("set-option") == []


def test_restart_agent_session_kills_dead_session_then_arms_remain():
    fake = FakeRun()
    sup = BoxSupervisor(_config(), run=fake)
    assert sup.restart_agent_session() is True
    # kill-session PRECEDES the fresh new-session so the dead-pane name is reusable.
    kinds = [c[1] for c in fake.calls if c[1] in ("kill-session", "new-session")]
    assert kinds == ["kill-session", "new-session"]
    assert fake.sub_calls("set-option") == [
        ["tmux", "set-option", "-t", "kanibako", "remain-on-exit", "on"]
    ]


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

def test_on_detach_noop_does_nothing_and_never_raises():
    sup = BoxSupervisor(_config(), run=FakeRun())
    assert sup._on_detach() is None
    sup._safe_on_detach()  # no exception


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
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue"]
    ]


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
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue"]
    ]
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
    assert fake.sub_calls("new-session") == [
        ["tmux", "new-session", "-d", "-s", "kanibako", "--", "claude", "--continue"]
    ]


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
