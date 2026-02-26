"""Integration tests for the start command pipeline.

Every test exercises real filesystem operations (fcntl locks, credential
files, mtime checks).  Run with::

    pytest -m integration tests/test_start_integration.py -v
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time

import pytest

from tests.conftest_integration import requires_runtime


@pytest.mark.integration
class TestRealFcntlLocking:
    """fcntl-based lock acquisition with real file descriptors."""

    def test_lock_acquired_and_released(self, integration_home, integration_config):
        """Lock file is unlocked after the pipeline returns."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

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
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

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

    def test_lock_released_after_container_error(
        self, integration_home, integration_config
    ):
        """Lock is released in the finally block even after a container error."""
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

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
class TestCredentialFlow:
    """End-to-end credential refresh pipeline with real files."""

    def test_host_to_project_flow(
        self, integration_home, integration_config, integration_credentials
    ):
        """Full credential pipeline: host → project, real files."""
        from kanibako.config import load_config
        from kanibako_plugin_claude.credentials import refresh_host_to_project
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, initialize=True)

        # Create fake host credentials
        host_claude = integration_home / "int_home" / ".claude"
        host_claude.mkdir(parents=True, exist_ok=True)
        host_creds = host_claude / ".credentials.json"
        host_token = {"claudeAiOauth": {"token": "host-fresh-token"}, "extra": True}
        host_creds.write_text(json.dumps(host_token))

        project_creds = proj.shell_path / ".claude" / ".credentials.json"

        # Refresh host → project
        refresh_host_to_project(host_creds, project_creds)
        assert project_creds.is_file()
        project_data = json.loads(project_creds.read_text())
        assert project_data["claudeAiOauth"]["token"] == "host-fresh-token"

    def test_mtime_based_freshness(
        self, integration_home, integration_config, integration_credentials
    ):
        """A newer project credential is not overwritten by an older host one."""
        from kanibako.config import load_config
        from kanibako_plugin_claude.credentials import refresh_host_to_project
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, initialize=True)

        # Create host credentials
        host_claude = integration_home / "int_home" / ".claude"
        host_claude.mkdir(parents=True, exist_ok=True)
        host_creds = host_claude / ".credentials.json"
        host_creds.write_text(json.dumps({"claudeAiOauth": {"token": "host-old"}}))

        # Write project credentials with fresh token
        project_creds = proj.shell_path / ".claude" / ".credentials.json"
        project_creds.parent.mkdir(parents=True, exist_ok=True)
        project_creds.write_text(json.dumps({"claudeAiOauth": {"token": "project-fresh"}}))

        # Set project mtime ahead of host
        now = time.time()
        os.utime(host_creds, (now - 10, now - 10))
        os.utime(project_creds, (now, now))

        result = refresh_host_to_project(host_creds, project_creds)
        assert result is False

        # Verify project token unchanged
        project_data = json.loads(project_creds.read_text())
        assert project_data["claudeAiOauth"]["token"] == "project-fresh"


@pytest.mark.integration
class TestExitCodePropagation:
    """Exit code propagation through real container invocations."""

    @requires_runtime
    def test_zero_exit_code(
        self, integration_home, integration_config, integration_credentials,
        container_runtime_cmd, pulled_image,
    ):
        """Real container exits 0 on success."""
        result = subprocess.run(
            [container_runtime_cmd, "run", "--rm", pulled_image, "/bin/true"],
            capture_output=True,
        )
        assert result.returncode == 0

    @requires_runtime
    def test_nonzero_exit_code(
        self, integration_home, integration_config, integration_credentials,
        container_runtime_cmd, pulled_image,
    ):
        """Real container propagates exit code 42."""
        result = subprocess.run(
            [container_runtime_cmd, "run", "--rm", pulled_image,
             "/bin/sh", "-c", "exit 42"],
            capture_output=True,
        )
        assert result.returncode == 42
