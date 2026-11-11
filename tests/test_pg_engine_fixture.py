"""Pure controls for the PG engine fixture's scanners, public setup, and CI receipt gate."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.e2e.pg_fixture import (
  PrivateEnv,
  SecretProbe,
  finish,
  register_agent,
  required_cases_passed,
)


@pytest.mark.parametrize("expired", [False, True])
def test_finish_bounds_exited_worker_with_descendant_held_pipes(tmp_path, expired):
  ready = tmp_path / "child-pid"
  child = (
    "import os, signal, time; from pathlib import Path; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
  )
  parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-I', '-c', {child!r}])"
  launched = time.monotonic()
  process = subprocess.Popen(
    [sys.executable, "-I", "-c", parent],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
  )
  try:
    while not ready.exists() and time.monotonic() < launched + 2:
      time.sleep(0.01)
    assert ready.exists(), "owned pipe-holder did not start"
    process.wait(timeout=1)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="bounded PG launch subprocess timed out"):
      finish(process, SecretProbe(b"unused-control"), launched + (0 if expired else 6))
    assert time.monotonic() - started < 3
    assert process.stdout.closed and process.stderr.closed
    child_pid = int(ready.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
      status = Path(f"/proc/{child_pid}/stat")
      if not status.exists() or status.read_text().split()[2] == "Z":
        break
      time.sleep(0.01)
    else:
      pytest.fail("owned pipe-holder survived bounded shutdown")
  finally:
    try:
      os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
    process.wait(timeout=1)
    process.stdout.close()
    process.stderr.close()


def test_scanner_rejects_material_across_chunks_without_echoing(tmp_path):
  value = b"synthetic-scanner-control"
  (tmp_path / "leak").write_bytes(b"x" * (65536 - 5) + value)
  probe = SecretProbe(value)
  with pytest.raises(AssertionError) as error:
    probe.tree(tmp_path)
  assert value.decode() not in str(error.value)
  assert value.decode() not in repr(probe)
  assert value.decode() not in repr(PrivateEnv(TOKEN=value.decode()))


def test_scanner_does_not_follow_escaping_links(tmp_path):
  outside = tmp_path / "outside"
  outside.mkdir()
  (outside / "secret").write_bytes(b"synthetic-link-control")
  fixture = tmp_path / "fixture"
  fixture.mkdir()
  (fixture / "file-link").symlink_to(outside / "secret")
  (fixture / "directory-link").symlink_to(outside, target_is_directory=True)
  (fixture / "receipt").write_text("digest only")
  probe = SecretProbe(b"synthetic-link-control")
  probe.tree(fixture)
  assert probe.files == 1


def test_public_pg_setup_loads_a_real_registered_agent(tmp_path):
  from persona_grata import AgentStore

  root = tmp_path / "store"
  provider = Path(__file__).parent / "e2e" / "fixtures" / "pg-credential"
  register_agent(root, provider, "http://127.0.0.1:12345")
  store = AgentStore(root)
  assert set(store.agents()) == {"pipeproof"}
  loaded = store.load("pipeproof")["agent"]
  assert loaded["harness"]["name"] == "claude"
  assert loaded["harness"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"
  assert loaded["harness"]["config_store"]["env"]["ANTHROPIC_BASE_URL"].startswith(
    "http://127.0.0.1:12345"
  )
  assert "ANTHROPIC_AUTH_TOKEN" not in loaded["harness"]["config_store"]["env"]
  assert "--manager" in loaded["secret"]["fetch"]


@pytest.mark.parametrize("fault", [None, "missing", "skipped", "failure", "error", "duplicate"])
def test_required_receipts_reject_absence_and_nonpass(tmp_path, fault):
  from xml.etree.ElementTree import Element, SubElement, ElementTree

  suite = Element("testsuite")
  names = ["test_pg_pipe[supervised]", "test_pg_pipe[direct]", "test_pg_pipe_cleanup"]
  if fault == "missing":
    names.pop()
  if fault == "duplicate":
    names.append(names[0])
  for name in names:
    case = SubElement(suite, "testcase", classname="tests.e2e.test_pg_credential_pipe", name=name)
    if fault in {"skipped", "failure", "error"}:
      SubElement(case, fault)
  report = tmp_path / "results.xml"
  ElementTree(suite).write(report)
  assert required_cases_passed(report) is (fault is None)
