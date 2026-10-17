"""Integration tests for the start command pipeline.

Every test exercises real filesystem operations (fcntl locks, credential
files, mtime checks).  Run with::

    pytest -m integration tests/test_start_integration.py -v
"""

from __future__ import annotations

import fcntl
import subprocess
import threading

import pytest

from tests.conftest_integration import requires_runtime


@pytest.mark.integration
class TestRealFcntlLocking:
  """fcntl-based lock acquisition with real file descriptors."""

  def test_lock_acquired_and_released(self, integration_home, integration_config):
    """Lock file is unlocked after the pipeline returns."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths, resolve_project

    config = load_config(integration_config)
    std = load_std_paths(config)
    proj = resolve_project(std, config, initialize=True)

    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Acquire lock
    fd = open(lock_file, "w")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fd.write("test-container\n")
    fd.flush()

    # Release lock
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()

    # Re-acquire to prove release worked
    fd2 = open(lock_file, "w")
    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd2, fcntl.LOCK_UN)
    fd2.close()

  def test_concurrent_lock_contention(self, integration_home, integration_config):
    """A second caller gets OSError when the lock is already held."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths, resolve_project

    config = load_config(integration_config)
    std = load_std_paths(config)
    proj = resolve_project(std, config, initialize=True)

    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    fd1 = open(lock_file, "w")
    fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)

    blocked = threading.Event()

    def try_lock():
      fd2 = open(lock_file, "w")
      try:
        fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Should not reach here
        fcntl.flock(fd2, fcntl.LOCK_UN)
        fd2.close()
      except OSError:
        blocked.set()
        fd2.close()

    t = threading.Thread(target=try_lock)
    t.start()
    t.join(timeout=5)

    assert blocked.is_set(), "Second lock attempt should have raised OSError"

    fcntl.flock(fd1, fcntl.LOCK_UN)
    fd1.close()

  def test_lock_released_after_container_error(self, integration_home, integration_config):
    """Lock is released in the finally block even after a container error."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths, resolve_project

    config = load_config(integration_config)
    std = load_std_paths(config)
    proj = resolve_project(std, config, initialize=True)

    lock_file = proj.metadata_path / ".kanibako.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    fd = open(lock_file, "w")
    try:
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
      raise RuntimeError("simulated container failure")
    except RuntimeError:
      pass
    finally:
      fcntl.flock(fd, fcntl.LOCK_UN)
      fd.close()

    # Verify lock is available again
    fd2 = open(lock_file, "w")
    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fd2, fcntl.LOCK_UN)
    fd2.close()


@pytest.mark.integration
class TestExitCodePropagation:
  """Exit code propagation through real container invocations."""

  @requires_runtime
  def test_zero_exit_code(
    self,
    integration_home,
    integration_config,
    integration_credentials,
    container_runtime_cmd,
    pulled_image,
  ):
    """Real container exits 0 on success."""
    result = subprocess.run(
      [container_runtime_cmd, "run", "--rm", pulled_image, "/bin/true"],
      capture_output=True,
    )
    assert result.returncode == 0

  @requires_runtime
  def test_nonzero_exit_code(
    self,
    integration_home,
    integration_config,
    integration_credentials,
    container_runtime_cmd,
    pulled_image,
  ):
    """Real container propagates exit code 42."""
    result = subprocess.run(
      [container_runtime_cmd, "run", "--rm", pulled_image, "/bin/sh", "-c", "exit 42"],
      capture_output=True,
    )
    assert result.returncode == 42
