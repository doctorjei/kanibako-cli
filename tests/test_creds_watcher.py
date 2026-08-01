"""Tests for kanibako.creds_watcher — the per-box host credential-writeback watcher (D).

The PURE decision (:func:`decide_watch`) is exercised exhaustively; the watcher LOOP
is driven through injected callables (box-liveness probe, flag reader/clearer,
writeback, sleep) so no real box / FS / store is touched and nothing waits.  The flag
helpers are driven over a real tmp dir (they are tiny + tolerant).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.creds_watcher import (
    CREDS_DIRTY_RELPATH,
    CredsWatcher,
    WatchAction,
    clear_creds_dirty,
    creds_dirty_flag_path,
    creds_store_lock,
    decide_watch,
    read_creds_dirty,
)


# --------------------------------------------------------------------------- #
# The pure decision (the heart).                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "running,dirty,expected",
    [
        (True, True, WatchAction.WRITEBACK),
        (True, False, WatchAction.NONE),
        (False, True, WatchAction.FINAL_WRITEBACK),
        (False, False, WatchAction.EXIT),
    ],
)
def test_decide_watch_table(running, dirty, expected):
    assert decide_watch(running, dirty) is expected


# --------------------------------------------------------------------------- #
# Flag helpers (tolerant).                                                      #
# --------------------------------------------------------------------------- #

def test_flag_path_uses_the_shared_relpath(tmp_path):
    assert creds_dirty_flag_path(tmp_path) == tmp_path / CREDS_DIRTY_RELPATH


def test_read_creds_dirty_true_only_when_present(tmp_path):
    assert read_creds_dirty(tmp_path) is False
    flag = creds_dirty_flag_path(tmp_path)
    flag.parent.mkdir(parents=True)
    flag.write_text("1")
    assert read_creds_dirty(tmp_path) is True


def test_clear_creds_dirty_removes_and_is_idempotent(tmp_path):
    flag = creds_dirty_flag_path(tmp_path)
    flag.parent.mkdir(parents=True)
    flag.write_text("1")
    clear_creds_dirty(tmp_path)
    assert not flag.exists()
    clear_creds_dirty(tmp_path)  # already gone -> no raise
    assert not flag.exists()


def test_flag_helpers_tolerate_errors(monkeypatch):
    # A path whose probe raises must degrade to False / silent, never crash.
    boom = Path("/definitely/not/writable/creds")

    def raiser(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(Path, "exists", raiser)
    assert read_creds_dirty(boom) is False  # swallowed
    monkeypatch.undo()
    monkeypatch.setattr(Path, "unlink", raiser)
    clear_creds_dirty(boom)  # swallowed, no raise


# --------------------------------------------------------------------------- #
# The watcher loop (injected deps; no real box).                                #
# --------------------------------------------------------------------------- #

class _Deps:
    """Scriptable injected deps for CredsWatcher, recording every effect.

    The ``writeback`` fake MODELS the real
    :func:`kanibako.commands.start.writeback_session_credentials`: on SUCCESS it clears
    the (stateful) dirty flag itself (it is the sole clearer, D Part 3); on FAILURE it
    raises WITHOUT clearing, so the flag stays set for a retry.  The watcher NEVER
    clears the flag directly, so the fake carries no separate clear op.
    """

    def __init__(self, *, running, dirty, writeback_raises=False):
        # ``running`` is a list consumed one-per-tick (last value sticks); ``dirty`` is
        # a single MUTABLE flag state (the real flag) the writeback self-clears.
        self._running = list(running)
        self.dirty = dirty
        self.writeback_calls = 0
        self.sleeps = 0
        self._writeback_raises = writeback_raises

    @staticmethod
    def _take(seq):
        return seq[0] if len(seq) == 1 else seq.pop(0)

    def is_running(self):
        return self._take(self._running)

    def read_dirty(self):
        return self.dirty

    def writeback(self):
        self.writeback_calls += 1
        if self._writeback_raises:
            raise RuntimeError("store write blew up")  # failure: flag NOT cleared
        self.dirty = False  # success: the writeback self-clears the flag

    def sleep(self, _s):
        self.sleeps += 1


def _watcher(deps: _Deps) -> CredsWatcher:
    return CredsWatcher(
        is_running=deps.is_running,
        read_dirty=deps.read_dirty,
        writeback=deps.writeback,
        sleep=deps.sleep,
        poll_interval=0.0,
    )


def test_loop_writes_back_on_dirty_then_exits_once_flag_self_clears():
    # tick1: box up + dirty -> writeback (self-clears); tick2: box down clean -> exit.
    deps = _Deps(running=[True, False], dirty=True)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 1
    assert deps.dirty is False  # cleared by the successful writeback
    assert deps.sleeps == 1  # slept once (after tick1), exited on tick2


def test_loop_clean_box_up_does_nothing_until_box_down():
    # tick1: box up + clean -> nothing; tick2: box down clean -> exit (no writeback).
    deps = _Deps(running=[True, False], dirty=False)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 0


def test_loop_box_down_dirty_does_a_final_writeback_then_exits():
    # box already gone with a pending signal -> one FINAL writeback (self-clears), exit.
    deps = _Deps(running=[False], dirty=True)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 1
    assert deps.dirty is False
    assert deps.sleeps == 0  # exited immediately, never polled again


def test_loop_box_down_clean_exits_immediately_no_writeback():
    deps = _Deps(running=[False], dirty=False)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 0
    assert deps.sleeps == 0


def test_loop_survives_a_raising_writeback():
    # A writeback failure is logged-not-crashed: the loop keeps going and still exits
    # cleanly when the box drops.
    deps = _Deps(running=[True, False], dirty=True, writeback_raises=True)
    assert _watcher(deps).run() == 0  # no exception escapes


def test_loop_failed_writeback_leaves_flag_set_and_retries():
    # MUTATION-PROOF (the rotation-lockout guarantee): a FAILED writeback must NOT
    # clear the flag, so the pending refresh signal is RETRIED on the next tick — not
    # dropped.  box up (fails) -> box up (fails again) -> box down (final, fails):
    # the flag stays dirty throughout and the writeback is retried EVERY tick.
    deps = _Deps(running=[True, True, False], dirty=True, writeback_raises=True)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 3  # retried each tick (never dropped)
    assert deps.dirty is True  # never cleared on failure


def test_loop_successful_writeback_clears_flag_and_stops_retrying():
    # MUTATION-PROOF counterpart: a SUCCESSFUL writeback self-clears the flag, so the
    # next tick sees it clean and does NOT write back again (no duplicate propagation).
    deps = _Deps(running=[True, True, False], dirty=True)
    assert _watcher(deps).run() == 0
    assert deps.writeback_calls == 1  # exactly one write; flag cleared, no re-fire
    assert deps.dirty is False


# --------------------------------------------------------------------------- #
# main(): single-instance lock + private-box skip (re-resolution mocked out).   #
# --------------------------------------------------------------------------- #

class _FakeAuth:
    def __init__(self, creds_shared):
        self.creds_shared = creds_shared


class _FakeProj:
    def __init__(self, tmp_path):
        self.metadata_path = tmp_path
        self.shell_path = tmp_path / "home"
        self.project_path = tmp_path / "proj"


def test_main_skips_a_private_box(monkeypatch, tmp_path):
    import kanibako.creds_watcher as cw

    proj = _FakeProj(tmp_path)
    monkeypatch.setattr(
        cw, "_resolve_watch_context",
        lambda box: (object(), proj, "kanibako-x", object(), _FakeAuth(creds_shared=False)),
    )
    # A private box returns 0 WITHOUT ever taking the lock or looping.
    called = {"lock": 0}
    monkeypatch.setattr(cw, "_single_instance_lock", lambda p: called.__setitem__("lock", called["lock"] + 1) or object())
    assert cw.main(["--box", "x"]) == 0
    assert called["lock"] == 0  # never reached lock acquisition


def test_main_exits_when_another_watcher_holds_the_lock(monkeypatch, tmp_path):
    import kanibako.creds_watcher as cw

    proj = _FakeProj(tmp_path)
    monkeypatch.setattr(
        cw, "_resolve_watch_context",
        lambda box: (object(), proj, "kanibako-x", object(), _FakeAuth(creds_shared=True)),
    )
    monkeypatch.setattr(cw, "_single_instance_lock", lambda p: None)  # lock held
    ran = {"run": 0}
    monkeypatch.setattr(CredsWatcher, "run", lambda self: ran.__setitem__("run", 1) or 0)
    assert cw.main(["--box", "x"]) == 0
    assert ran["run"] == 0  # never started the loop (lock unavailable)


def test_main_no_context_returns_zero(monkeypatch):
    import kanibako.creds_watcher as cw

    monkeypatch.setattr(cw, "_resolve_watch_context", lambda box: None)
    assert cw.main(["--box", "gone"]) == 0


def test_creds_store_lock_serializes_and_releases(tmp_path, monkeypatch):
    # The store lock is a real flock context manager: it acquires + releases cleanly
    # and, once released, can be re-entered (it does not deadlock a sequential caller).
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with creds_store_lock():
        pass
    with creds_store_lock():  # re-entrant across sequential calls (released above)
        pass
    assert (tmp_path / "state" / "kanibako" / "creds-writeback.lock").exists()


def test_creds_store_lock_tolerates_a_bad_lock_path(tmp_path, monkeypatch):
    # A lock path that cannot be created must degrade to proceeding UNLOCKED, not raise.
    blocker = tmp_path / "state"
    blocker.write_text("i am a file, not a dir")  # mkdir under it will fail
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    with creds_store_lock():  # no raise; yields unlocked
        pass


def test_single_instance_lock_is_exclusive(tmp_path):
    import kanibako.creds_watcher as cw

    lock_path = tmp_path / ".kanibako-creds-watcher.lock"
    first = cw._single_instance_lock(lock_path)
    assert first is not None
    # A SECOND attempt on the same path (held by ``first``) is refused.
    assert cw._single_instance_lock(lock_path) is None
    first.close()  # release
    # After release, a fresh attempt succeeds again.
    third = cw._single_instance_lock(lock_path)
    assert third is not None
    third.close()


# ---------------------------------------------------------------------------
# A NO-AGENT box (D-M6) has no stamp — the watcher must not resolve auth for it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stamp", [None, ""])
def test_no_watch_context_without_an_agent_stamp(monkeypatch, stamp):
    """⚑ The writeback-side half of the P7 credential-path fix.

    ``_resolve_watch_context`` feeds the auth resolve ``{"system.agent": <stamp>}``,
    so an EMPTY stamp would rebuild the COLLAPSED ``<auth>/`` source that MUST-1
    exists to prevent. A box with ``pref.system.agent: null`` (D-M6) carries no
    stamp at all, so the guard must return BEFORE the auth resolve — pinned here
    for both falsy shapes ``inspect_env`` can yield.
    """
    from unittest.mock import MagicMock, patch

    from kanibako.creds_watcher import _resolve_watch_context

    runtime = MagicMock()
    runtime.inspect_env.return_value = stamp
    # The resolver imports its collaborators INSIDE the function, so patch them at
    # their DEFINING modules (patching ``creds_watcher.X`` would not intercept).
    with (
        patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
        patch("kanibako.config.load_config"),
        patch("kanibako.paths.load_std_paths"),
        patch("kanibako.paths.resolve_box_target", return_value=MagicMock()),
        patch("kanibako.commands.start._resolve_box_auth_source") as m_auth,
        patch("kanibako.targets.resolve_target") as m_target,
    ):
        assert _resolve_watch_context(box=None) is None
        m_auth.assert_not_called()
        m_target.assert_not_called()
