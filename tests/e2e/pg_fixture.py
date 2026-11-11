"""Secret-free receipts and disposable public-PG configuration for engine tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import time


class PrivateEnv(dict):
  def __repr__(self) -> str:
    return "<isolated subprocess environment>"


@dataclass(repr=False)
class SecretProbe:
  value: bytes = field(repr=False)
  surfaces: int = 0
  files: int = 0

  @property
  def digest(self) -> str:
    return hashlib.sha256(self.value).hexdigest()

  def check(self, content: bytes) -> None:
    __tracebackhide__ = True
    self.surfaces += 1
    if self.value in content:
      raise AssertionError("synthetic credential escaped the one-shot transport")

  def tree(self, root: Path) -> None:
    """Scan regular fixture files without following directory or file symlinks."""

    def inaccessible(error: OSError) -> None:
      raise AssertionError("fixture scan could not read a directory") from None

    for _, _, names, directory in os.fwalk(root, follow_symlinks=False, onerror=inaccessible):
      for name in names:
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
          continue
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
          tail = b""
          while chunk := stream.read(65536):
            self.check(tail + chunk)
            tail = chunk[-len(self.value) :]
        self.files += 1


def finish(process, probe, deadline):
  """Bound output collection and shutdown of this fixture-owned process group."""
  __tracebackhide__ = True
  try:
    stdout, stderr = process.communicate(timeout=max(0, deadline - time.monotonic() - 5))
  except subprocess.TimeoutExpired as error:
    stdout, stderr = error.output or b"", error.stderr or b""
    try:
      for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, float("inf"))):
        try:
          os.killpg(process.pid, sig)
        except ProcessLookupError:
          pass
        try:
          stdout, stderr = process.communicate(
            timeout=max(0, min(grace, deadline - time.monotonic() - 0.25))
          )
        except subprocess.TimeoutExpired as error:
          stdout, stderr = error.output or b"", error.stderr or b""
    finally:
      for stream in (process.stdout, process.stderr):
        if stream is not None:
          stream.close()
      try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
      except subprocess.TimeoutExpired:
        pass
    probe.check(stdout)
    probe.check(stderr)
    raise AssertionError("bounded PG launch subprocess timed out") from None
  probe.check(stdout)
  probe.check(stderr)
  return stdout, stderr


def register_agent(root: Path, provider: Path, endpoint: str) -> None:
  from persona_grata import AgentStore, make_agent
  from persona_grata.composition import write_document
  import yapyon

  store = AgentStore(root)
  command = shlex.join([sys.executable, "-I", str(provider), "--manager"])
  write_document(
    yapyon.parse(f"secret<base.secret>:\n  name: 'fixture'\n  fetch: {command!r}\n"),
    store.component_path(("fixture", "secret")),
  )
  store.configure(defaults={"secret": {"backend": "fixture"}})
  definition = make_agent("minimax", "claude")
  definition.merge(
    yapyon.parse(
      f"agent:\n  persona:\n    mind:\n      endpoint: {endpoint!r}\n"
      "      model_list: ['pipe-model']\n"
    )
  )
  store.create("pipeproof", definition)


def required_cases_passed(report: Path) -> bool:
  """Require all three engine cases to finish, including when session fixtures skip."""
  from xml.etree import ElementTree

  expected = {"test_pg_pipe[supervised]", "test_pg_pipe[direct]", "test_pg_pipe_cleanup"}
  cases = [
    case
    for case in ElementTree.parse(report).iter("testcase")
    if "test_pg_credential_pipe" in case.get("classname", "")
  ]
  return (
    len(cases) == len(expected)
    and {case.get("name") for case in cases} == expected
    and all(not list(case) or all(child.tag == "properties" for child in case) for case in cases)
  )


if __name__ == "__main__":
  if not required_cases_passed(Path(sys.argv[1])):
    raise SystemExit("required PG engine cases did not all pass")
  print("PG engine receipts: supervised, direct, cleanup passed")
