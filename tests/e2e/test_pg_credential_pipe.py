"""Real PG manager → inherited pipe → initial receiving-agent environment."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

import pytest

from tests.e2e.conftest import (
  E2E_IMAGE,
  SUBPROCESS_TIMEOUT,
  box_container,
  run_kanibako,
  write_e2e_settings_files,
)
from tests.e2e.pg_fixture import PrivateEnv, SecretProbe, finish, register_agent

pytestmark = pytest.mark.e2e
FIXTURE = Path(__file__).parent / "fixtures" / "pg-credential"


@pytest.fixture
def pg_launch(e2e_env):
  probe = SecretProbe(secrets.token_urlsafe(48).encode())
  accepted = []

  class Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *args):
      pass

    def do_GET(self):
      supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
      authorized = hashlib.sha256(supplied.encode()).hexdigest() == probe.digest
      accepted.append(authorized)
      self.send_response(200 if authorized else 401)
      self.send_header("Content-Type", "application/json")
      self.end_headers()
      self.wfile.write(b'{"data": [{"id": "pipe-model"}]}')

    def do_POST(self):
      self.rfile.read(int(self.headers.get("Content-Length", "0")))
      self.do_GET()

  server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  env = PrivateEnv(e2e_env["env"])
  e2e_env["env"] = env
  for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    env.pop(key, None)
  (e2e_env["home"] / ".claude" / ".credentials.json").unlink()
  launcher = e2e_env["home"] / ".local" / "bin" / "claude"
  shutil.copyfile(FIXTURE, launcher)
  launcher.chmod(0o755)
  root = e2e_env["config_home"] / "pg-fixture"
  calls = e2e_env["tmp_path"] / "manager-calls"
  register_agent(root, FIXTURE, f"http://127.0.0.1:{server.server_port}")
  env.update(AGENT_STORE_DIR=str(root), PG_E2E_VALUE=probe.value.decode(), PG_E2E_CALLS=str(calls))
  write_e2e_settings_files(
    e2e_env["config_home"],
    e2e_env["data_home"],
    home=e2e_env["home"],
    agent="pipeproof",
  )
  try:
    yield e2e_env, probe, root, calls, accepted
  finally:
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    # Check even on an earlier assertion failure; never echo a credential-bearing surface.
    probe.tree(e2e_env["tmp_path"])


def engine(env, *args, probe, deadline=None):
  limit = time.monotonic() + 10
  if deadline is not None:
    limit = min(limit, deadline - 5)
  if time.monotonic() >= limit:
    raise AssertionError("PG worker deadline exhausted")
  process = subprocess.Popen(
    ["podman", *args],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
  )
  stdout, stderr = finish(process, probe, limit)
  return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def sample_argv(pid, probe):
  pending = [pid]
  count = 0
  while pending:
    child = pending.pop()
    try:
      probe.check(Path(f"/proc/{child}/cmdline").read_bytes())
      count += 1
      pending.extend(
        int(p) for p in Path(f"/proc/{child}/task/{child}/children").read_text().split()
      )
    except FileNotFoundError:
      pass
  return count


def metadata(env, name, probe, deadline, marker=None):
  inspected = engine(env, "inspect", name, probe=probe, deadline=deadline)
  probe.check(inspected.stdout)
  probe.check(inspected.stderr)
  if inspected.returncode or not json.loads(inspected.stdout)[0]["State"]["Running"]:
    return False
  observed = marker is None
  for args in (("logs", name), ("top", name, "args")):
    result = engine(env, *args, probe=probe, deadline=deadline)
    probe.check(result.stdout)
    probe.check(result.stderr)
    if result.returncode:
      raise AssertionError("could not collect live container logs/argv")
    if args[0] == "logs" and marker is not None:
      observed = marker in result.stdout
  return observed


def verify_calls(calls, probe):
  probe.check(calls.read_bytes())
  assert calls.read_text().splitlines() == [probe.digest], "PG manager must fetch exactly once"


@pytest.mark.parametrize("mode", ["supervised", "direct"])
def test_pg_pipe(pg_launch, mode):
  fixture, probe, _, calls, accepted = pg_launch
  env, project = fixture["env"], fixture["project"]
  box = f"e2e_pg_{mode}"
  created = run_kanibako(["create", str(project), "--name", box], env)
  probe.check(created.stdout.encode())
  probe.check(created.stderr.encode())
  assert created.returncode == 0, "real CLI create failed"
  assert not calls.exists(), "create unexpectedly fetched the manager credential"
  args = [
    "kanibako",
    "start",
    box,
    "--no-auto-auth",
    "--detach" if mode == "supervised" else "--ephemeral",
  ]
  deadline = time.monotonic() + SUBPROCESS_TIMEOUT
  process = subprocess.Popen(
    args,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
  )
  receipt_path = project / "pg-receipt.json"
  inspected = False
  samples = 0
  try:
    while time.monotonic() < deadline - 5 and not receipt_path.exists():
      samples += sample_argv(process.pid, probe)
      status = process.poll()
      if status is not None and (status != 0 or mode == "direct"):
        break
      time.sleep(0.1)
    if receipt_path.exists():
      inspected = metadata(env, box_container(box), probe, deadline)
    assert receipt_path.exists(), "initial receiving agent wrote no receipt"
    probe.check(receipt_path.read_bytes())
    receipt = json.loads(receipt_path.read_text())
    assert receipt == {
      "digest": probe.digest,
      "present": True,
      "fds_closed": True,
      "supervised": mode == "supervised",
    }
    assert inspected, "receiving container was not inspected while alive"
    assert samples > 0, "launch argv was not sampled"
    verify_calls(calls, probe)
    assert any(accepted), "synthetic endpoint never authorized the PG credential"
    probe.tree(fixture["tmp_path"])
  finally:
    (project / "pg-release").touch()
    finish(process, probe, deadline)
  assert process.returncode == 0, "real CLI start failed"
  if mode == "supervised":
    stopped = run_kanibako(["stop", box], env)
    probe.check(stopped.stdout.encode())
    probe.check(stopped.stderr.encode())
    assert stopped.returncode == 0, "real CLI stop failed"
  assert engine(env, "inspect", box_container(box), probe=probe).returncode != 0
  print(
    f"PG pipe receipt: {mode}; files={probe.files}; surfaces={probe.surfaces}; argv_samples={samples}"
  )


def test_pg_pipe_cleanup(pg_launch):
  fixture, probe, root, calls, _ = pg_launch
  env = fixture["env"]
  name = box_container("e2e_pg_fault")
  args = [
    sys.executable,
    "-I",
    str(FIXTURE),
    "--fault-host",
    str(root),
    E2E_IMAGE,
    str(fixture["home"]),
    str(fixture["project"]),
    name,
  ]
  deadline = time.monotonic() + SUBPROCESS_TIMEOUT
  process = subprocess.Popen(
    args,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
  )
  created = False
  samples = 0
  try:
    while process.poll() is None and time.monotonic() < deadline - 5:
      samples += sample_argv(process.pid, probe)
      if not created and metadata(env, name, probe, deadline, b'"fault_drained": true'):
        created = True
        released = engine(env, "kill", "--signal", "USR1", name, probe=probe, deadline=deadline)
        probe.check(released.stdout)
        probe.check(released.stderr)
        assert released.returncode == 0, "could not release the inspected fault receiver"
      time.sleep(0.1)
  finally:
    stdout, _ = finish(process, probe, deadline)
  assert process.returncode == 0, "negative runtime control did not fail as expected"
  receipts = [json.loads(line) for line in stdout.decode().splitlines() if line.startswith("{")]
  assert any(item.get("fault_drained") for item in receipts), (
    "container never reached fault receiver"
  )
  assert any(item.get("receiver_fds_closed") for item in receipts), (
    "receiver left transport FDs open"
  )
  host = next(item for item in receipts if "publication_failed" in item)
  assert host["publication_failed"] and host["host_fds_closed"]
  assert host["elapsed"] < 40, "publication failure exceeded its cleanup budget"
  assert created and samples > 0, "real container creation/launch argv not observed"
  verify_calls(calls, probe)
  absent = engine(env, "inspect", name, probe=probe, deadline=deadline)
  probe.check(absent.stdout)
  probe.check(absent.stderr)
  assert absent.returncode != 0, "owned fault container survived failed publication"
  probe.tree(fixture["tmp_path"])
  print(
    f"PG pipe receipt: cleanup; files={probe.files}; surfaces={probe.surfaces}; argv_samples={samples}"
  )
