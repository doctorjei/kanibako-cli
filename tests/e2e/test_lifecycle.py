"""E2E lifecycle tests for kanibako container management.

These tests exercise the real kanibako CLI against real podman.
Each test creates a project, launches a container, verifies behavior,
and cleans up.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    podman_exec,
    run_kanibako,
    wait_for_container,
    SUBPROCESS_TIMEOUT,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


class TestPersistentLaunch:
    """Test 1: Persistent mode launch and tmux session creation."""

    def test_persistent_container_runs_with_tmux(self, e2e_env):
        """kanibako start → container running with tmux session."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        # Create a project
        result = run_kanibako(
            ["create", str(project), "--name", "e2e-persist"],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Start in persistent mode with stub in long-running mode.
        # The start command will try to tmux attach, which will fail
        # (no TTY), but the container should be running.
        result = run_kanibako(
            ["start", "e2e-persist",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )
        # start may return non-zero because tmux attach fails without TTY,
        # but the container should still be running.

        # Verify container is running
        container_name = "kanibako-e2e-persist"
        wait_for_container(container_name, timeout=15)

        # Verify tmux session exists inside the container
        ps_result = podman_exec(
            container_name, ["tmux", "list-sessions"]
        )
        assert ps_result.returncode == 0, (
            f"tmux list-sessions failed: {ps_result.stderr}"
        )
        assert "kanibako" in ps_result.stdout

    def test_persistent_stop_cleans_up(self, e2e_env):
        """kanibako stop removes the persistent container."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-stop"],
            env=env,
        )
        assert result.returncode == 0

        run_kanibako(
            ["start", "e2e-stop",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-stop", timeout=15)

        # Stop it
        result = run_kanibako(["stop", "e2e-stop"], env=env)
        assert result.returncode == 0, f"stop failed: {result.stderr}"

        # Verify container is gone
        inspect = subprocess.run(
            ["podman", "inspect", "kanibako-e2e-stop"],
            capture_output=True,
            timeout=5,
        )
        assert inspect.returncode != 0, "Container should not exist after stop"


class TestEphemeralLaunch:
    """Test 2: Ephemeral mode launch with correct entrypoint."""

    def test_ephemeral_runs_stub_and_exits(self, e2e_env):
        """kanibako start --ephemeral → stub runs, exits cleanly."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-ephemeral"],
            env=env,
        )
        assert result.returncode == 0

        result = run_kanibako(
            ["start", "e2e-ephemeral", "--ephemeral",
             "-e", "CLAUDE_STUB_MODE=session",
             "-e", "CLAUDE_STUB_SLEEP=1"],
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )
        assert result.returncode == 0, (
            f"ephemeral start failed (rc={result.returncode}): "
            f"{result.stderr}"
        )
        # Verify the stub ran (not /bin/bash)
        assert "claude-stub" in result.stdout, (
            f"Expected stub banner in stdout, got: {result.stdout!r}"
        )


class TestReattach:
    """Test 3: Second start reattaches to existing session."""

    def test_only_one_container_exists(self, e2e_env):
        """Starting twice does not create a second container."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-reattach"],
            env=env,
        )
        assert result.returncode == 0

        # First start
        run_kanibako(
            ["start", "e2e-reattach",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-reattach", timeout=15)

        # Second start (reattach attempt — will fail without TTY)
        run_kanibako(
            ["start", "e2e-reattach"],
            env=env,
        )

        # Verify only one container with this name
        ps_result = subprocess.run(
            ["podman", "ps", "-a",
             "--filter", "name=kanibako-e2e-reattach",
             "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        names = [n.strip() for n in ps_result.stdout.strip().splitlines() if n.strip()]
        assert len(names) == 1, f"Expected 1 container, got: {names}"


class TestShell:
    """Tests 4-5: Shell access to running containers."""

    def test_shell_one_shot_command(self, e2e_env):
        """kanibako shell -- echo hello → returns output."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-shell"],
            env=env,
        )
        assert result.returncode == 0

        # Start persistent container
        run_kanibako(
            ["start", "e2e-shell",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-shell", timeout=15)

        # Run a one-shot command via shell
        result = run_kanibako(
            ["shell", "e2e-shell", "--", "echo", "hello-from-shell"],
            env=env,
        )
        assert result.returncode == 0, f"shell failed: {result.stderr}"
        assert "hello-from-shell" in result.stdout


class TestStopAndRestart:
    """Test 6: Stop removes container, restart creates fresh one."""

    def test_restart_gets_new_container(self, e2e_env):
        """After stop, a new start creates a different container."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-restart"],
            env=env,
        )
        assert result.returncode == 0

        # First start
        run_kanibako(
            ["start", "e2e-restart",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-restart", timeout=15)

        # Get first container ID
        id1 = subprocess.run(
            ["podman", "inspect", "--format", "{{.Id}}",
             "kanibako-e2e-restart"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        # Stop
        run_kanibako(["stop", "e2e-restart"], env=env)

        # Start again
        run_kanibako(
            ["start", "e2e-restart",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-restart", timeout=15)

        # Get second container ID
        id2 = subprocess.run(
            ["podman", "inspect", "--format", "{{.Id}}",
             "kanibako-e2e-restart"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        assert id1 != id2, "Expected different container IDs after restart"
