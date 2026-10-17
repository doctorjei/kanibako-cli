"""A container stuck part-way out of the runtime (podman's ``removing`` state).

A ``podman rm`` killed part-way leaves its container in ``removing``: its layer is
half-deleted, so ``inspect`` exits 125, yet it still holds its name, so ``podman run``
refuses the name and ``ps -a`` still lists it.  kanibako read state only through
``inspect`` and ``ps`` without ``-a``, so ``list`` said ``stopped``, ``stop`` blamed a
lock file, and a launch died in ``podman run``.  Every door now reads ``ps -a`` and
names the runtime's own cure, ``podman rm -f <name>``; none removes it for the user.

⚑ The runtime here is a shell stub named by ``KANIBAKO_DOCKER_CMD`` that answers as
podman 5.4.2 did on the reproduction (bifrost): ``inspect`` and ``stop`` exit 125,
``ps -a`` prints ``removing``, ``ps`` without ``-a`` prints nothing.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.start import _run_container
from kanibako.runtime.container import ContainerRuntime
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
  BoxMode,
  _early_scope,
  load_primary_boxes,
  load_std_paths,
  resolve_project,
)
from kanibako.utils import WORKSET_SEGMENT_PRIMARY, container_name_for_box_name


@pytest.fixture(autouse=True)
def _kanibako_logger_keeps_its_handlers():
  """``cli.main`` installs a handler on this test's stderr buffer; hand it back."""
  logger = logging.getLogger("kanibako")
  saved = list(logger.handlers)
  yield
  logger.handlers[:] = saved


def _stub(tmp_path: Path, container: str, state: str = "removing") -> tuple[Path, Path]:
  """A podman stand-in whose *container* is stuck in *state*; returns (stub, call log).

  ⚑ ``inspect`` fails on a ``removing`` container (half-deleted layer) and succeeds on a
  ``stopping`` one, which ``ps`` without ``-a`` does not list (it is not running).
  """
  log = tmp_path / "runtime-calls.log"
  inspect = (
    "echo '[{}]'; exit 0"
    if state == "stopping"
    else """echo 'Error: reading information about layer "45ec": layer not known' >&2
    exit 125"""
  )
  stub = tmp_path / "podman"
  stub.write_text(f"""#!/bin/sh
echo "$*" >> {log}
case "$1" in
  ps)
    case " $* " in
      *" -a "*) case "$*" in *State*) printf '%s\\t{state}\\n' {container} ;; esac ;;
    esac
    exit 0 ;;
  inspect)
    {inspect} ;;
  stop)
    echo 'Error: running or paused, refusing to clean up: container state improper' >&2
    exit 125 ;;
  run)
    echo 'Error: the container name "{container}" is already in use' >&2
    exit 125 ;;
esac
exit 0
""")
  stub.chmod(0o755)
  return stub, log


@pytest.fixture
def stuck_box(config_file, tmp_home, credentials_dir, tmp_path, monkeypatch, capsys):
  """A primary box whose container is stuck in ``removing``: (box name, container, log)."""
  config = load_config(config_file)
  std = load_std_paths(config)
  workspace = tmp_home / "wedge"
  workspace.mkdir()
  resolve_project(std, config, project_dir=str(workspace), initialize=True)
  name = next(
    n
    for n, w in load_primary_boxes(
      std.primary_workset, early=_early_scope(std, BoxMode.primary)
    ).items()
    if Path(w).name == "wedge"
  )
  container = container_name_for_box_name(name, WORKSET_SEGMENT_PRIMARY)
  assert container is not None
  stub, log = _stub(tmp_path, container)
  monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
  capsys.readouterr()
  return name, container, log


def _cli(argv: list[str], capsys) -> tuple[int, str, str]:
  from kanibako import cli

  try:
    rc = cli.main(argv)
  except SystemExit as exc:
    rc = exc.code
  out = capsys.readouterr()
  return (rc or 0), out.out, out.err


def _calls(log: Path) -> list[str]:
  return log.read_text().splitlines() if log.exists() else []


# ---------------------------------------------------------------------------
# The reader.
# ---------------------------------------------------------------------------


class TestContainerState:
  def test_reads_ps_a_not_inspect(self, tmp_path):
    stub, log = _stub(tmp_path, "kb-primary-x")
    rt = ContainerRuntime(command=str(stub))
    assert rt.container_exists("kb-primary-x") is False  # inspect fails on it
    assert rt.container_state("kb-primary-x") == "removing"
    assert rt.container_states() == {"kb-primary-x": "removing"}
    assert not [c for c in _calls(log) if c.startswith("ps") and "-a" not in c.split()]

  def test_matches_the_name_exactly(self, tmp_path):
    stub, _log = _stub(tmp_path, "kb-primary-xy")
    assert ContainerRuntime(command=str(stub)).container_state("kb-primary-x") is None

  def test_a_failed_runtime_answers_none(self, tmp_path):
    rt = ContainerRuntime(command=str(tmp_path / "podman"))
    failed = subprocess.CompletedProcess([], 125, stdout="kb-a\tremoving\n", stderr="x")
    with patch("kanibako.runtime.container.subprocess.run", return_value=failed):
      assert rt.container_states() == {}
    with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError):
      assert rt.container_state("kb-a") is None


# ---------------------------------------------------------------------------
# list / box info / stop, through ``cli.main``.
# ---------------------------------------------------------------------------


def test_list_shows_removing_not_stopped(stuck_box, capsys):
  name, _container, _log = stuck_box
  rc, out, _err = _cli(["box", "list"], capsys)
  assert rc == 0
  rows = [line.split()[:2] for line in out.splitlines() if line.split()[:1] == [name]]
  assert rows == [[name, "removing"]], out


def test_box_info_names_the_state_and_the_cure(stuck_box, capsys):
  name, container, _log = stuck_box
  rc, out, _err = _cli(["box", "info", name], capsys)
  assert rc == 0, out
  assert f"removing, stuck holding the name ({container})" in out
  assert f"clear it: podman rm -f {container}" in out
  assert f"not running ({container})" not in out


def test_box_info_names_stopping_not_exited(stuck_box, tmp_path, capsys):
  """``inspect`` succeeds on a ``stopping`` container, so it must not read as exited."""
  name, container, _log = stuck_box
  _stub(tmp_path, container, "stopping")  # rewrites the stub the fixture points at
  rc, out, _err = _cli(["box", "info", name], capsys)
  assert rc == 0, out
  assert f"stopping, stuck holding the name ({container})" in out
  assert f"clear it: podman rm -f {container}" in out
  assert "exited, still holding the name" not in out


def test_stop_names_the_runtime_cure_not_the_lock(stuck_box, capsys):
  name, container, log = stuck_box
  rc, out, err = _cli(["stop", name], capsys)
  assert rc == 1
  assert "'removing'" in err and f"podman rm -f {container}" in err
  assert ".kanibako.lock" not in out + err
  assert "No running container found" not in out
  assert not [c for c in _calls(log) if c.startswith("rm")]  # never removed for the user


# ---------------------------------------------------------------------------
# start (persistent and ephemeral) and shell refuse before ``podman run``.
# ---------------------------------------------------------------------------


def _wedge(m, state="removing"):
  """The ``start_mocks`` runtime answering as the stub does for a stuck container."""
  m.runtime.cmd = "/usr/bin/podman"
  m.runtime.container_exists.return_value = False
  m.runtime.is_running.return_value = False
  m.runtime.container_state.return_value = state


@pytest.mark.parametrize(
  "persistent, entrypoint",
  [
    (True, None),  # start (persistent / --detach)
    (False, None),  # start --ephemeral
    (False, "/bin/sh"),  # shell
  ],
)
@pytest.mark.parametrize("state", ["removing", "stopping"])
def test_launch_refuses_before_run(start_mocks, capsys, persistent, entrypoint, state):
  with start_mocks() as m:
    _wedge(m, state)
    rc = _run_container(
      project_dir=None,
      entrypoint=entrypoint,
      image_override=None,
      new_session=False,
      safe_mode=False,
      resume_mode=False,
      extra_args=[],
      persistent=persistent,
    )
    assert rc == 1
    m.runtime.run.assert_not_called()
    m.runtime.rm.assert_not_called()
    names = {c.args[0] for c in m.runtime.container_state.call_args_list}
  err = capsys.readouterr().err
  assert f"{state!r} state" in err
  assert "Clear it:  podman rm -f " in err
  assert len(names) == 1 and next(iter(names)) in err


@pytest.mark.parametrize("state", ["paused", "exited"])
def test_persistent_start_stops_when_the_stale_container_will_not_go(
  start_mocks,
  capsys,
  state,
):
  """``rm``'s return is read: a container ``rm`` leaves behind (a ``paused`` one: not
  running, so no reattach, and ``rm`` without ``-f`` refuses it) stops the launch
  before ``run``, naming its state and the runtime's cure."""
  with start_mocks() as m:
    m.runtime.cmd = "/usr/bin/podman"
    m.runtime.container_state.return_value = state
    m.runtime.container_exists.return_value = True
    m.runtime.rm.return_value = False
    rc = _run_container(
      project_dir=None,
      entrypoint=None,
      image_override=None,
      new_session=False,
      safe_mode=False,
      resume_mode=False,
      extra_args=[],
      persistent=True,
    )
    assert rc == 1
    m.runtime.run.assert_not_called()
  err = capsys.readouterr().err
  assert "could not remove this project's old container" in err
  assert f"state {state!r}" in err
  assert "Clear it:  podman rm -f " in err


# ---------------------------------------------------------------------------
# stop: a failed ``rm`` is never reported as "Removed".
# ---------------------------------------------------------------------------


class TestStopReadsRm:
  @pytest.fixture
  def rt(self):
    from unittest.mock import MagicMock

    rt = MagicMock()
    rt.cmd = "/usr/bin/podman"
    rt.is_running.return_value = False
    rt.inspect_env.return_value = None
    rt.container_exists.return_value = True
    rt.container_state.return_value = "exited"
    rt.rm.return_value = False
    return rt

  def _stop(self, rt):
    from kanibako.commands.stop import _stop_one

    with (
      patch("kanibako.commands.stop.load_config"),
      patch("kanibako.commands.stop.load_std_paths"),
      patch("kanibako.commands.stop.resolve_box_target"),
      patch("kanibako.commands.stop.box_container", return_value="kb-primary-x"),
    ):
      return _stop_one(rt, project_dir=None)

  # ⚑ A failed ``rm`` that leaves the container: test_stop.py's
  # test_a_failed_removal_is_reported_and_fails_the_run (one carrier of that case).

  @pytest.mark.parametrize("stopped", [True, False])
  def test_a_container_gone_by_itself_is_not_a_failure(self, rt, capsys, stopped):
    """A ``--rm`` container's own removal can win the race after ``stop``."""
    rt.stop.return_value = stopped
    rt.container_exists.side_effect = [True, False]
    rt.container_state.return_value = None
    assert self._stop(rt) == 0
    assert "Clear it" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# stop --all names the same cure as stop for a container it could not remove.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("live", [True, False])
def test_stop_all_names_the_cure_for_a_stuck_container(capsys, live):
  from unittest.mock import MagicMock

  from kanibako.commands.stop import _stop_all

  rt = MagicMock()
  rt.cmd = "/usr/bin/podman"
  rt.list_running.return_value = [("kb-primary-x", "img", "Removing")]
  rt.find_labeled.return_value = []
  rt.is_running.return_value = live
  rt.stop.return_value = True
  # ⚑ ``inspect`` (``container_exists``) FAILS on a ``removing`` container.  Live: the
  # stopped container is there until the failed ``rm`` leaves it ``removing``; not live:
  # it was ``removing`` all along, so it never reads as existing.
  rt.container_exists.side_effect = [True, False] if live else [False]
  rt.rm.return_value = False
  rt.container_state.return_value = "removing"
  with patch("kanibako.commands.stop._boxes_rendering_no_name", return_value=[]):
    assert _stop_all(rt, force=True) == 1
  assert rt.container_exists.call_count == (2 if live else 0)
  err = capsys.readouterr().err
  assert "Failed to remove kb-primary-x: " in err
  assert "'removing' state (kb-primary-x)" in err
  assert "Clear it:  podman rm -f kb-primary-x" in err
