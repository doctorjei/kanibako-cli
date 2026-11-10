"""Tests for kanibako.launch.creds_watcher — the per-box host credential-writeback watcher (D).

The PURE decision (:func:`decide_watch`) is exercised exhaustively; the watcher LOOP
is driven through injected callables (box-liveness probe, flag reader/clearer,
writeback, sleep) so no real box / FS / store is touched and nothing waits.  The flag
helpers are driven over a real tmp dir (they are tiny + tolerant).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from kanibako.launch.creds_watcher import (
  CREDS_DIRTY_RELPATH,
  CredsWatcher,
  WatchAction,
  clear_creds_dirty,
  creds_dirty_flag_path,
  creds_store_lock,
  decide_watch,
  read_creds_dirty,
)


@pytest.fixture(autouse=True)
def _restore_kanibako_logger():
  """``main`` calls ``setup_logging``, which reconfigures the shared ``kanibako``
  logger; put it back so no later test inherits this module's handler."""
  import logging

  logger = logging.getLogger("kanibako")
  handlers, level = logger.handlers[:], logger.level
  yield
  logger.handlers[:] = handlers
  logger.setLevel(level)  # setLevel, not assignment: it clears the level cache


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


def test_flag_helpers_tolerate_errors(monkeypatch, tmp_path):
  # A boxfs probe that raises an OSError other than FileNotFoundError must
  # degrade to False / silent, never crash.
  from kanibako import boxfs

  calls = []

  def raiser(name):
    def _raise(*_a, **_k):
      calls.append(name)
      raise PermissionError("nope")

    return _raise

  monkeypatch.setattr(boxfs, "stat", raiser("stat"))
  monkeypatch.setattr(boxfs, "unlink", raiser("unlink"))
  assert read_creds_dirty(tmp_path) is False  # swallowed
  assert clear_creds_dirty(tmp_path) is None  # swallowed, no raise
  assert calls == ["stat", "unlink"]


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
  import kanibako.launch.creds_watcher as cw

  proj = _FakeProj(tmp_path)
  monkeypatch.setattr(
    cw,
    "_resolve_watch_context",
    lambda box: (object(), proj, "kanibako-x", object(), _FakeAuth(creds_shared=False)),
  )
  # A private box returns 0 WITHOUT ever taking the lock or looping.
  called = {"lock": 0}
  monkeypatch.setattr(
    cw,
    "_single_instance_lock",
    lambda p: called.__setitem__("lock", called["lock"] + 1) or object(),
  )
  assert cw.main(["--box", "x"]) == 0
  assert called["lock"] == 0  # never reached lock acquisition


def test_main_exits_when_another_watcher_holds_the_lock(monkeypatch, tmp_path):
  import kanibako.launch.creds_watcher as cw

  proj = _FakeProj(tmp_path)
  monkeypatch.setattr(
    cw,
    "_resolve_watch_context",
    lambda box: (object(), proj, "kanibako-x", object(), _FakeAuth(creds_shared=True)),
  )
  monkeypatch.setattr(cw, "_single_instance_lock", lambda p: None)  # lock held
  ran = {"run": 0}
  monkeypatch.setattr(CredsWatcher, "run", lambda self: ran.__setitem__("run", 1) or 0)
  assert cw.main(["--box", "x"]) == 0
  assert ran["run"] == 0  # never started the loop (lock unavailable)


def test_main_no_context_returns_zero(monkeypatch):
  import kanibako.launch.creds_watcher as cw

  monkeypatch.setattr(cw, "_resolve_watch_context", lambda box: None)
  assert cw.main(["--box", "gone"]) == 0


def _main_with_failing_auth_resolve(exc, *, stamp="claude"):
  """Run ``main`` through the REAL ``_resolve_watch_context`` for a box stamped
  *stamp* whose auth resolve raises *exc*; its collaborators are imported inside the
  function, so they are patched at their defining modules."""
  from unittest.mock import MagicMock, patch

  import kanibako.launch.creds_watcher as cw

  runtime = MagicMock()
  runtime.inspect_env.return_value = stamp
  with (
    patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
    patch("kanibako.settings.config.load_config"),
    patch("kanibako.settings.paths.load_std_paths"),
    patch("kanibako.settings.paths.resolve_box_target", return_value=MagicMock()),
    patch("kanibako.commands.start._resolve_box_auth_source", side_effect=exc),
    patch("kanibako.targets.resolve_target"),
    patch("kanibako.utils.box_container", return_value="kanibako-x"),
  ):
    return cw.main(["--box", "x"])


def _watcher_records(caplog):
  return [r for r in caplog.records if r.name == "kanibako.creds_watcher"]


@pytest.mark.parametrize("error", ["SettingsError", "ConfigError"])
def test_main_warns_on_a_settings_refusal(caplog, error):
  """🛑 A settings REFUSAL while resolving the box is the user's to fix, not a crash:
  the watcher logs it at WARNING with the refusal's own text (no stack trace) and
  exits 0 as before. Both refusal types: ``SettingsError`` (an undeclared key, a
  stray in ``agent.yaml``) and ``ConfigError`` (a file that is not valid YAML)."""
  import logging

  from kanibako.errors import ConfigError
  from kanibako.settings.settings_resolve import SettingsError

  exc_type, text = {
    "SettingsError": (
      SettingsError,
      "`model` at the top level of /cfg/agents/claude/agent.yaml is not a settings key",
    ),
    "ConfigError": (
      ConfigError,
      "the config file /cfg/agents/claude/agent.yaml is not valid YAML: line 2. "
      "Fix or remove the file, then retry.",
    ),
  }[error]
  with caplog.at_level(logging.DEBUG, logger="kanibako.creds_watcher"):
    assert _main_with_failing_auth_resolve(exc_type(text)) == 0
  [record] = _watcher_records(caplog)
  assert record.levelno == logging.WARNING
  assert record.exc_info is None
  assert "credential writeback skipped" in record.getMessage()
  assert text in record.getMessage()


def test_main_logs_any_other_resolve_failure_with_its_stack(caplog):
  """Every failure that is NOT a settings refusal keeps the old behavior: an ERROR
  carrying the stack trace, and exit 0."""
  import logging

  with caplog.at_level(logging.DEBUG, logger="kanibako.creds_watcher"):
    assert _main_with_failing_auth_resolve(RuntimeError("not a refusal")) == 0
  [record] = _watcher_records(caplog)
  assert record.levelno == logging.ERROR
  assert record.exc_info is not None


def test_main_does_not_call_a_malformed_stamp_a_settings_refusal(caplog):
  """A malformed ``KANIBAKO_AGENT`` stamp makes ``agent_address_node`` raise
  ``ConfigError`` BEFORE the auth resolve. That is not the box's settings, so the
  WARNING stays scoped to the resolve call: the old ERROR-with-stack path runs."""
  import logging

  with caplog.at_level(logging.DEBUG, logger="kanibako.creds_watcher"):
    assert (
      _main_with_failing_auth_resolve(
        AssertionError("the auth resolve must not be reached"),
        stamp="bad+",
      )
      == 0
    )
  [record] = _watcher_records(caplog)
  assert record.levelno == logging.ERROR
  assert record.exc_info is not None
  assert "credential writeback skipped" not in record.getMessage()


@contextlib.contextmanager
def _stderr_to(path: Path) -> Iterator[None]:
  """Point ``sys.stderr`` at *path*, as the spawner does for the detached watcher.

  ⚑ Entered in the test BODY, not a fixture: pytest's capture reinstalls its own
  ``sys.stderr`` between fixture setup and the call.
  """
  real = sys.stderr
  with open(path, "a") as sink:
    sys.stderr = sink
    try:
      yield
    finally:
      sys.stderr = real


def test_main_writes_a_settings_refusal_to_its_stderr_log(tmp_path):
  """🛑 The watcher runs DETACHED, so the stderr file the spawner opened is the only
  place its WARNING reaches the user. ``main`` configures logging for that file: the
  refusal lands there whole, stamped with its time and level."""
  import re

  from kanibako.settings.settings_resolve import SettingsError

  text = "`model` at the top level of /cfg/agents/claude/agent.yaml is not a settings key"
  log_file = tmp_path / "box.creds-watcher.log"
  with _stderr_to(log_file):
    assert _main_with_failing_auth_resolve(SettingsError(text)) == 0
  logged = log_file.read_text()
  assert re.match(
    r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING creds watcher for box "
    r"kanibako-x: credential writeback skipped",
    logged,
  ), logged
  assert text in logged


def test_main_logs_a_repeated_record_once(tmp_path):
  """A writeback that keeps failing logs the same WARNING every tick; the watcher's
  log records it once, so the file does not grow for as long as the box runs."""
  from kanibako.log import get_logger

  log_file = tmp_path / "box.creds-watcher.log"
  with _stderr_to(log_file):
    _main_with_failing_auth_resolve(RuntimeError("unresolvable"))
    log = get_logger("start")
    for _ in range(3):
      log.warning("Credential writeback failed: %s", "disk full")
    log.warning("Credential writeback failed: %s", "store locked")
  logged = log_file.read_text()
  assert logged.count("disk full") == 1
  assert logged.count("store locked") == 1


def _held_exclusively(directory: Path) -> bool:
  """True iff *directory* is flocked by somebody else (probed non-blockingly)."""
  fd = os.open(directory, os.O_RDONLY)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except BlockingIOError:
    return True
  else:
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False
  finally:
    os.close(fd)


def test_creds_store_lock_flocks_the_destination_dir(tmp_path):
  # The lock IS the destination directory: while held it EXCLUDES another writer,
  # it creates NO file of its own, and it releases cleanly on exit.
  dest = tmp_path / "store"
  dest.mkdir()
  with creds_store_lock(dest):
    assert _held_exclusively(dest)
    assert list(dest.iterdir()) == []  # a directory flock leaves no sentinel
  assert not _held_exclusively(dest)


def test_creds_store_lock_holds_every_destination(tmp_path):
  # A global-synced workset writeback touches its workset dir AND host home, so both
  # are locked — the lock's scope is the whole set of directories written.
  workset, home = tmp_path / "workset", tmp_path / "home"
  workset.mkdir()
  home.mkdir()
  with creds_store_lock(workset, home):
    assert _held_exclusively(workset)
    assert _held_exclusively(home)
  assert not _held_exclusively(workset)
  assert not _held_exclusively(home)


def test_creds_store_lock_does_not_self_deadlock_on_an_aliased_destination(tmp_path):
  # flock is per OPEN FILE DESCRIPTION, so opening one directory twice in one process
  # would BLOCK FOREVER.  Destinations are deduplicated by real path; run the
  # acquisition off-thread so a regression FAILS here instead of hanging the suite.
  dest = tmp_path / "store"
  dest.mkdir()
  alias = tmp_path / "alias"
  alias.symlink_to(dest)
  done = threading.Event()

  def _acquire() -> None:
    with creds_store_lock(dest, alias, dest):
      pass
    done.set()

  threading.Thread(target=_acquire, daemon=True).start()
  assert done.wait(timeout=10), "creds_store_lock self-deadlocked on one directory"


def test_creds_store_lock_tolerates_an_unusable_destination(tmp_path):
  # A destination that cannot be opened must degrade to proceeding UNLOCKED, not
  # raise — and must not conjure the directory it failed to lock.
  missing = tmp_path / "gone"
  with creds_store_lock(missing):  # no raise; yields unlocked
    pass
  assert not missing.exists()


def test_creds_store_lock_with_no_destinations_is_a_no_op():
  # A private-tier writeback writes nowhere; there is nothing to serialize.
  with creds_store_lock():
    pass


def test_single_instance_lock_is_exclusive(tmp_path):
  import kanibako.launch.creds_watcher as cw

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

  from kanibako.launch.creds_watcher import _resolve_watch_context

  runtime = MagicMock()
  runtime.inspect_env.return_value = stamp
  # The resolver imports its collaborators INSIDE the function, so patch them at
  # their DEFINING modules (patching ``creds_watcher.X`` would not intercept).
  with (
    patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
    patch("kanibako.settings.config.load_config"),
    patch("kanibako.settings.paths.load_std_paths"),
    patch("kanibako.settings.paths.resolve_box_target", return_value=MagicMock()),
    patch("kanibako.commands.start._resolve_box_auth_source") as m_auth,
    patch("kanibako.targets.resolve_target") as m_target,
  ):
    assert _resolve_watch_context(box=None) is None
    m_auth.assert_not_called()
    m_target.assert_not_called()


@pytest.mark.parametrize("stamp", ["navigator+claude", "navigator℘claude"])
def test_watch_context_canonicalizes_the_stamp_before_deriving(stamp):
  """The watcher's half of canonicalize-on-read (twin of ``test_stop.py``'s).

  ``KANIBAKO_AGENT`` carries the OUTSIDE spelling (``+``) because a human reads
  it in the box; ``harness_of`` splits on ``℘`` ALONE, so the raw stamp would
  send ``resolve_target`` after a plugin named ``navigator+claude``.  Unlike
  ``stop``, this one is NOT under a blanket catch at this level — it would take
  the watcher down instead of failing quietly — but the fix is the same and so
  is the seam.

  ⚑ BOTH SPELLINGS: ``+`` is what this version stamps, ``℘`` what an
  already-running older box carries.  Identical behavior is the back-compat
  guarantee.
  """
  from unittest.mock import MagicMock, patch

  from kanibako.launch.creds_watcher import _resolve_watch_context

  runtime = MagicMock()
  runtime.inspect_env.return_value = stamp
  with (
    patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
    patch("kanibako.settings.config.load_config"),
    patch("kanibako.settings.paths.load_std_paths"),
    patch("kanibako.settings.paths.resolve_box_target", return_value=MagicMock()),
    patch("kanibako.commands.start._resolve_box_auth_source") as m_auth,
    patch("kanibako.targets.resolve_target") as m_target,
  ):
    assert _resolve_watch_context(box=None) is not None
    # The plugin is keyed by the HARNESS.
    assert m_target.call_args.args[0] == "claude"
    # The agent-tier discriminator and the §1A selection level take the NODE.
    kwargs = m_auth.call_args.kwargs
    assert kwargs["agent_name"] == "navigator℘claude"
    assert kwargs["agent_identity"].node == "navigator℘claude"
    assert kwargs["agent_identity"].target_name == "claude"
    assert kwargs["selection_level"] == {"system.agent": "navigator℘claude"}


# ---------------------------------------------------------------------------
# The ONE carrier of the running box's agent stamp and its writeback context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  "stamp, node",
  [
    (None, None),
    ("", None),
    ("Navigator+Claude", "Navigator℘claude"),
    ("navigator℘claude", "navigator℘claude"),
    ("shell", "shell"),
  ],
)
def test_running_box_agent_reads_the_stamp_as_a_node(stamp, node):
  """Absent/empty ⇒ ``None``; otherwise the folded node, from either separator."""
  from unittest.mock import MagicMock

  from kanibako.launch.creds_watcher import AGENT_STAMP_ENV, running_box_agent

  runtime = MagicMock()
  runtime.inspect_env.return_value = stamp
  assert running_box_agent(runtime, "box-c") == node
  # The read side of the contract the launch writes as ``system.env.KANIBAKO_AGENT``.
  assert AGENT_STAMP_ENV == "KANIBAKO_AGENT"
  runtime.inspect_env.assert_called_once_with("box-c", AGENT_STAMP_ENV)


def test_running_box_agent_refuses_a_malformed_stamp():
  """A malformed stamp raises ``ConfigError``; each caller decides whether it is said."""
  from unittest.mock import MagicMock

  from kanibako.errors import ConfigError
  from kanibako.launch.creds_watcher import running_box_agent

  runtime = MagicMock()
  runtime.inspect_env.return_value = "a+b+c"
  with pytest.raises(ConfigError):
    running_box_agent(runtime, "box-c")


def test_box_writeback_context_keys_the_target_by_harness_and_auth_by_node():
  from unittest.mock import MagicMock, patch

  from kanibako.launch.creds_watcher import box_writeback_context

  std, proj = MagicMock(), MagicMock()
  with (
    patch("kanibako.commands.start._resolve_box_auth_source") as m_auth,
    patch("kanibako.targets.resolve_target") as m_target,
  ):
    target, auth_src = box_writeback_context(std, proj, "navigator℘claude")
  assert (target, auth_src) == (m_target.return_value, m_auth.return_value)
  m_target.assert_called_once_with("claude")
  kwargs = m_auth.call_args.kwargs
  assert kwargs["std"] is std and kwargs["proj"] is proj
  assert kwargs["agent_name"] == "navigator℘claude"
  assert kwargs["system_settings_path"] is std.settings
  assert kwargs["selection_level"] == {"system.agent": "navigator℘claude"}


def test_every_stamp_reader_goes_through_running_box_agent():
  """stop and both ``code`` legs read the stamp through ONE function, not their own."""
  from unittest.mock import MagicMock, patch

  from kanibako.commands import code_cmd, stop
  from kanibako.launch import creds_watcher

  runtime = MagicMock()
  runtime.inspect_env.side_effect = AssertionError("read the stamp directly")
  with patch.object(
    creds_watcher,
    "running_box_agent",
    return_value="navigator℘claude",
  ) as m_read:
    with patch.object(code_cmd, "_extension_for_agent", return_value="ext") as m_ext:
      assert (
        code_cmd._resolve_box_vscode_extension(
          runtime,
          MagicMock(),
          MagicMock(),
          "box-c",
        )
        == "ext"
      )
    m_ext.assert_called_once_with("claude")
    with (
      patch.object(creds_watcher, "box_writeback_context", return_value=("T", "A")) as m_ctx,
      patch("kanibako.commands.start.writeback_session_credentials") as m_wb,
    ):
      stop._writeback_on_stop(
        runtime,
        "P",
        "box-c",
        std="S",
        config=None,
        box_is_live=True,
      )
    m_ctx.assert_called_once_with("S", "P", "navigator℘claude")
    m_wb.assert_called_once_with("T", "P", auth_src="A")
    engine = MagicMock()
    engine.inspect_creation_metadata.return_value = ("remote-id", {})
    engine.container_image.return_value = "img"
    with (
      patch.object(code_cmd, "_extension_for_agent", return_value=None) as m_ext,
      patch.object(code_cmd, "_write_attached_config"),
      patch.object(code_cmd, "attached_container_config_path"),
    ):
      code_cmd._seed_remote_attached_config(engine, "box-c")
    m_ext.assert_called_once_with("claude")
  assert [c.args[1] for c in m_read.call_args_list] == ["box-c", "box-c", "remote-id"]
  engine.inspect_creation_metadata.assert_called_once_with("box-c")
  engine.container_image.assert_called_once_with("remote-id")


def test_writeback_context_preserves_registered_identity(tmp_path):
  from unittest.mock import MagicMock, patch

  from kanibako.agent_ref import AgentIdentity
  from kanibako.launch.creds_watcher import box_writeback_context
  from kanibako.settings.agent_config import agent_settings_path
  from kanibako.settings.config import AgentResolution

  identity = AgentIdentity(
    node="navigator",
    registered_name="Navigator",
    persona="Guide",
    harness="Claude",
    target_name="claude",
    entry_path=tmp_path / "PG" / "Navigator",
  )
  std, proj = MagicMock(), MagicMock()
  std.agents = tmp_path / "config" / "agents"
  with (
    patch(
      "kanibako.settings.config.resolve_agent_input", return_value=AgentResolution(identity)
    ) as m_identity,
    patch("kanibako.targets.resolve_target") as m_target,
    patch("kanibako.commands.start._resolve_box_auth_source") as m_auth,
  ):
    result = box_writeback_context(std, proj, "navigator")
  assert result == (m_target.return_value, m_auth.return_value)
  m_identity.assert_called_once_with(explicit_agent="navigator")
  m_target.assert_called_once_with("claude")
  assert m_auth.call_args.kwargs == {
    "std": std,
    "proj": proj,
    "agent_name": "navigator",
    "agent_identity": identity,
    "persona_values": None,
    "system_settings_path": std.settings,
    "agent_cfg_path": agent_settings_path(std.agents, "navigator"),
    "selection_level": {"system.agent": "navigator"},
  }


@pytest.fixture
def registered_auth_context(tmp_path, monkeypatch):
  from types import SimpleNamespace
  from unittest.mock import Mock

  from kanibako import persona_store, targets
  from kanibako.plugins.claude import ClaudeTarget
  from kanibako.settings import config_io, settings_launch as sl
  from kanibako.settings.settings_resolve import ResolveCtx

  std = SimpleNamespace(settings=tmp_path / "settings.yaml", agents=tmp_path / "agents")
  proj = SimpleNamespace(mode=SimpleNamespace(value="named"))
  config_io.dump_doc(std.settings, {"system": {"agent": "goose"}})
  native = {
    "env": {
      "AUTH_STORE": str(tmp_path / "live-auth"),
      "ANTHROPIC_BASE_URL": "https://synthetic.invalid",
    }
  }
  root = tmp_path / "captured-PG"
  location = Mock(return_value=root)
  listing = Mock(return_value=["Navigator"])
  load = Mock(
    side_effect=lambda name, *, root: (
      root / name,
      {
        "agent": {
          "persona": {"name": "Guide"},
          "harness": {
            "name": "claude",
            "config_store": {"env": dict(native["env"])},
            "auth_var": "ANTHROPIC_API_KEY",
          },
          "secret": {"backend": "synthetic-manager"},
        }
      },
    )
  )
  fetch = Mock(side_effect=AssertionError("auth-source resolution must not fetch a secret"))
  projection = Mock(wraps=persona_store.project_registered_persona)
  monkeypatch.setattr(targets, "discover_targets", lambda: {"claude": ClaudeTarget})
  monkeypatch.setattr(config_io, "persona_store_location", location)
  monkeypatch.setattr(config_io, "persona_store_agents", listing)
  monkeypatch.setattr(config_io, "load_persona_agent", load)
  monkeypatch.setattr(config_io, "fetch_persona_secret", fetch)
  monkeypatch.setattr(persona_store, "project_registered_persona", projection)

  def inputs(**kwargs):
    identity = kwargs["agent_identity"]
    assert identity.node == "navigator"
    assert kwargs["agent_name"] == "navigator"
    return sl.LaunchInputs(
      sl.ResolveSubject.BOX,
      ResolveCtx(identity.node, "ReviewSet", str(tmp_path), {}),
      std.settings,
      {"workset.auth.path": "{agent.navigator.env.AUTH_STORE}"},
      {},
      {},
      {},
      {
        "meta.box.agent.auth.share_support": True,
        "system.auth.share_allowed": True,
        "workset.auth.share_allowed": True,
        "workset.auth.global_sync": True,
        "box.auth.global_enabled": True,
        "box.auth.workset_enabled": True,
        "meta.box.auth.workset_path": "{workset.auth.path}/{system.agent}",
      },
      None,
      None,
      (),
      agent_identity=identity,
    )

  monkeypatch.setattr(sl, "resolve_inputs", inputs)
  yield SimpleNamespace(
    std=std,
    proj=proj,
    native=native,
    root=root,
    location=location,
    listing=listing,
    load=load,
    fetch=fetch,
    projection=projection,
  )
  fetch.assert_not_called()


def test_registered_stamp_resolves_live_auth_once_per_operation(registered_auth_context):
  from kanibako.launch.creds_watcher import box_writeback_context

  ctx = registered_auth_context
  for index, directory in enumerate(("live-auth", "changed-auth"), 1):
    ctx.native["env"]["AUTH_STORE"] = str(ctx.root.parent / directory)
    target, auth = box_writeback_context(ctx.std, ctx.proj, "navigator")
    assert target.name == "claude"
    assert auth.tier == "workset"
    assert auth.workset_source == str(ctx.root.parent / directory / "navigator")
    assert ctx.location.call_count == index
    assert ctx.listing.call_count == index
    assert ctx.load.call_count == index
    assert ctx.projection.call_count == index
    assert ctx.load.call_args.args == ("Navigator",)
    assert ctx.load.call_args.kwargs == {"root": ctx.root}
    captured, projected_target = ctx.projection.call_args.args
    assert captured.store_root == ctx.root
    assert captured.identity.entry_path == ctx.root / "Navigator"
    assert projected_target is target
    assert not ctx.std.agents.exists()
  ctx.fetch.assert_not_called()


def test_registered_stamp_auth_keeps_file_precedence(registered_auth_context):
  from kanibako.launch.creds_watcher import box_writeback_context
  from kanibako.settings.agent_config import agent_settings_path
  from kanibako.settings.config_io import dump_doc, load_doc

  ctx = registered_auth_context
  path = agent_settings_path(ctx.std.agents, "navigator")
  contents = {"self": {"env": {"AUTH_STORE": str(ctx.root.parent / "file-auth")}}}
  dump_doc(path, contents)
  target, auth = box_writeback_context(ctx.std, ctx.proj, "navigator")
  assert target.name == "claude"
  assert auth.workset_source == str(ctx.root.parent / "file-auth" / "navigator")
  assert load_doc(path) == contents
  ctx.load.assert_called_once_with("Navigator", root=ctx.root)
  assert ctx.projection.call_count == 1
  ctx.fetch.assert_not_called()


def test_stop_writeback_uses_registered_stamp_live_auth(registered_auth_context):
  from unittest.mock import Mock, patch

  from kanibako.commands.stop import _writeback_on_stop

  ctx = registered_auth_context
  runtime = Mock()
  runtime.inspect_env.return_value = "Navigator"
  with patch("kanibako.commands.start.writeback_session_credentials") as writeback:
    _writeback_on_stop(runtime, ctx.proj, "running-id", std=ctx.std, config=None, box_is_live=True)
  writeback.assert_called_once()
  assert writeback.call_args.args[0].name == "claude"
  assert writeback.call_args.args[1] is ctx.proj
  auth = writeback.call_args.kwargs["auth_src"]
  assert auth.tier == "workset"
  assert auth.workset_source == str(ctx.root.parent / "live-auth" / "navigator")
  ctx.load.assert_called_once_with("Navigator", root=ctx.root)
  assert ctx.projection.call_count == 1
  ctx.fetch.assert_not_called()


@pytest.mark.parametrize("node", ["claude", "shell"])
def test_native_stamp_context_never_reads_pg(monkeypatch, node):
  from unittest.mock import MagicMock, Mock, patch

  from kanibako import persona_store, targets
  from kanibako.launch.creds_watcher import box_writeback_context
  from kanibako.plugins.claude import ClaudeTarget
  from kanibako.targets.shell import ShellTarget

  forbidden = Mock(side_effect=AssertionError("native stamps never open PG"))
  monkeypatch.setattr(persona_store, "read_registered_persona", forbidden)
  monkeypatch.setattr(
    targets, "discover_targets", lambda: {"claude": ClaudeTarget, "shell": ShellTarget}
  )
  with patch("kanibako.commands.start._resolve_box_auth_source") as auth:
    target, result = box_writeback_context(MagicMock(), MagicMock(), node)
  assert target.name == node
  assert result is auth.return_value
  assert auth.call_args.kwargs["agent_name"] == node
  assert auth.call_args.kwargs["persona_values"] is None
  forbidden.assert_not_called()


def test_remote_attach_does_not_resolve_local_pg(tmp_path, monkeypatch):
  from unittest.mock import Mock, patch

  from kanibako.commands import code_cmd
  from kanibako.settings import config, config_io

  forbidden = Mock(side_effect=AssertionError("remote attach cannot use local PG"))
  monkeypatch.setattr(config, "resolve_agent_input", forbidden)
  for name in ("persona_store_location", "persona_store_agents", "load_persona_agent"):
    monkeypatch.setattr(config_io, name, forbidden)
  engine = Mock()
  engine.inspect_creation_metadata.return_value = ("remote-id", {})
  engine.container_image.return_value = "remote-image"
  engine.inspect_env.return_value = "Guide+claude"
  with (
    patch.object(
      code_cmd, "attached_container_config_path", return_value=tmp_path / "remote.json"
    ),
    patch.object(code_cmd, "_extension_for_agent", return_value="remote-ext") as extension,
    patch.object(code_cmd, "_write_attached_config") as write,
  ):
    code_cmd._seed_remote_attached_config(engine, "remote-name")
  engine.inspect_env.assert_called_once_with("remote-id", "KANIBAKO_AGENT")
  extension.assert_called_once_with("claude")
  write.assert_called_once_with(tmp_path / "remote.json", "remote-ext")
  forbidden.assert_not_called()
