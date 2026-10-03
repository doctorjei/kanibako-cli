"""Import-time side effects of the e2e conftest.

The plain ``pytest tests/`` unit run COLLECTS ``tests/e2e/`` — its tests are
deselected by ``addopts``, but collection imports the package's conftest anyway.
A subprocess the conftest starts at import is therefore started by every unit run,
on every developer box, under whatever HOME that run inherited.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

E2E_CONFTEST = "tests.e2e.conftest"


def _recorder(calls: list[list[str]]):
  def record(cmd, **kwargs):
    calls.append([str(a) for a in cmd])
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

  return record


def test_importing_e2e_conftest_starts_no_subprocess(monkeypatch):
  """Importing the e2e conftest starts no child process.

  Pins the runtime-availability probes — ``podman info`` for the host store and
  ``podman image inspect`` for the image — behind fixtures, so they are answered at
  test SETUP. A host missing podman, tmux or the image skips its e2e tests; it
  never costs a unit run two subprocesses.
  """
  calls: list[list[str]] = []
  monkeypatch.delitem(sys.modules, E2E_CONFTEST, raising=False)
  monkeypatch.setattr(subprocess, "run", _recorder(calls))

  importlib.import_module(E2E_CONFTEST)

  assert calls == [], f"import spawned {len(calls)} subprocess(es): {calls}"


def test_host_storage_probe_runs_at_most_once(monkeypatch):
  """The host-store probe answers every caller from one subprocess, per session.

  ``host_storage_conf`` and ``ensure_image_in_pinned_store`` both need the host's
  real graphroot, and it cannot change mid-session, so the probe is memoized rather
  than repeated per caller.
  """
  calls: list[list[str]] = []
  monkeypatch.setattr(subprocess, "run", _recorder(calls))
  e2e = importlib.import_module(E2E_CONFTEST)
  e2e._host_storage.cache_clear()

  first = e2e._host_storage()
  second = e2e._host_storage()

  assert first is second, "probe result differs between callers"
  assert len(calls) <= 1, f"probe ran {len(calls)} times for 2 callers: {calls}"
