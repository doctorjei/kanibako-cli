"""Integration tests for kanibako CLI lifecycle operations.

Tests exercise the kanibako CLI via subprocess calls in isolated environments.
Run with::

    pytest -m integration tests/test_lifecycle_integration.py -v
"""

from __future__ import annotations

import subprocess
import time

import pytest

from tests.conftest_integration import requires_runtime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMEOUT = 120  # seconds — generous for CI


def _run_kanibako(
    *args: str,
    env: dict[str, str],
    cwd: str | None = None,
    timeout: int = _TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run the kanibako CLI as a subprocess with the given environment."""
    return subprocess.run(
        ["kanibako", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _setup_with_image(cli_env: dict, image: str) -> None:
    """Run kanibako setup and override the configured container image."""
    result = _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
    assert result.returncode == 0, f"setup failed: {result.stderr}"

    # Patch the config to use the requested image.
    config_file = cli_env["config_home"] / "kanibako.toml"
    text = config_file.read_text()
    import re

    text = re.sub(r'^image\s*=\s*"[^"]*"', f'image = "{image}"', text, flags=re.MULTILINE)
    config_file.write_text(text)


# =========================================================================
# Setup
# =========================================================================


@pytest.mark.integration
class TestKanibakoSetup:
    """Verify kanibako setup creates expected files and dirs."""

    def test_setup_creates_config_and_dirs(self, cli_env):
        """kanibako setup creates config file, agents dir, and env file."""
        result = _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert result.returncode == 0, f"setup failed: {result.stderr}"

        config_file = cli_env["config_home"] / "kanibako.toml"
        assert config_file.is_file(), "kanibako.toml not created"

        data_path = cli_env["data_home"] / "kanibako"
        agents_dir = data_path / "agents"
        assert agents_dir.is_dir(), "agents dir not created"

        env_file = data_path / "env"
        assert env_file.is_file(), "env file not created"

    def test_setup_idempotent(self, cli_env):
        """Running setup twice succeeds without errors."""
        r1 = _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert r1.returncode == 0

        r2 = _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
        assert r2.returncode == 0


# =========================================================================
# Image operations
# =========================================================================


@pytest.mark.integration
class TestKanibakoImageOps:
    """Verify kanibako image commands."""

    def test_image_list_runs(self, cli_env):
        """kanibako image list exits 0 after setup."""
        _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
        result = _run_kanibako(
            "image", "list", env=cli_env["env"], cwd=str(cli_env["project"])
        )
        assert result.returncode == 0

    @requires_runtime
    def test_image_build_base(self, cli_env):
        """kanibako image rebuild oci builds from bundled Containerfile."""
        _run_kanibako("setup", env=cli_env["env"], cwd=str(cli_env["project"]))
        result = _run_kanibako(
            "image", "rebuild", "oci",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
            timeout=600,
        )
        assert result.returncode == 0, f"image build failed: {result.stderr}"


# =========================================================================
# Shell (container execution)
# =========================================================================


@pytest.mark.integration
class TestKanibakoShell:
    """Verify kanibako can run commands inside containers.

    Uses ``busybox:latest`` as a lightweight image.  The ``start -c``
    flag overrides the entrypoint so the container runs a single command
    and exits.
    """

    @requires_runtime
    def test_shell_runs_command(self, cli_env, container_runtime_cmd):
        """kanibako start -c runs a command and captures output."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        result = _run_kanibako(
            "start", "-c", "/bin/sh", "--", "-c", "echo hello-from-container",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"start failed: {result.stderr}"
        assert "hello-from-container" in result.stdout

    @requires_runtime
    def test_shell_workspace_mounted(self, cli_env, container_runtime_cmd):
        """The project directory is visible inside the container at /home/agent/workspace."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # Create a marker file in the project dir.
        marker = cli_env["project"] / "marker.txt"
        marker.write_text("workspace-ok\n")

        result = _run_kanibako(
            "start", "-c", "/bin/cat",
            "--", "/home/agent/workspace/marker.txt",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"start failed: {result.stderr}"
        assert "workspace-ok" in result.stdout

    @requires_runtime
    def test_shell_env_vars_applied(self, cli_env, container_runtime_cmd):
        """Environment variables from the env file are visible inside the container."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # Write a custom env var to the global env file.
        env_file = cli_env["data_home"] / "kanibako" / "env"
        env_file.write_text("MY_TEST_VAR=lifecycle-check\n")

        result = _run_kanibako(
            "start", "-c", "/bin/sh", "--", "-c", "echo $MY_TEST_VAR",
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
        )
        assert result.returncode == 0, f"start failed: {result.stderr}"
        assert "lifecycle-check" in result.stdout


# =========================================================================
# Lifecycle (start / stop)
# =========================================================================


@pytest.mark.integration
class TestKanibakoLifecycle:
    """Verify the start → running → stop → gone cycle."""

    @requires_runtime
    def test_start_stop_cycle(self, cli_env, container_runtime_cmd):
        """Start a container, verify it runs, stop it, verify it's gone."""
        _setup_with_image(cli_env, "busybox:latest")
        subprocess.run(
            [container_runtime_cmd, "pull", "busybox:latest"],
            capture_output=True, check=True,
        )

        # Launch kanibako in the background — the container runs `sleep`.
        proc = subprocess.Popen(
            [
                "kanibako", "start", "-c", "/bin/sleep", "--", "300",
            ],
            env=cli_env["env"],
            cwd=str(cli_env["project"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Poll until the container appears (up to 30 s).
            container_up = False
            for _ in range(30):
                time.sleep(1)
                ps = subprocess.run(
                    [container_runtime_cmd, "ps", "--format", "{{.Names}}"],
                    capture_output=True, text=True,
                )
                if any("kanibako" in name for name in ps.stdout.splitlines()):
                    container_up = True
                    break
            if not container_up:
                # Capture subprocess output for debugging CI failures.
                stdout = proc.stdout.read() if proc.poll() is not None else b""
                stderr = proc.stderr.read() if proc.poll() is not None else b""
                assert False, (
                    f"Container did not start within 30 s.\n"
                    f"Process exited: {proc.poll()}\n"
                    f"stdout: {stdout.decode(errors='replace')}\n"
                    f"stderr: {stderr.decode(errors='replace')}"
                )

            # Stop via kanibako CLI.
            stop_result = _run_kanibako(
                "stop", env=cli_env["env"], cwd=str(cli_env["project"])
            )
            assert stop_result.returncode == 0

            # Verify the container is gone.
            time.sleep(2)
            ps2 = subprocess.run(
                [container_runtime_cmd, "ps", "-a", "--format", "{{.Names}}"],
                capture_output=True, text=True,
            )
            assert not any(
                "kanibako" in name for name in ps2.stdout.splitlines()
            ), f"Container still present: {ps2.stdout}"
        finally:
            proc.kill()
            proc.wait(timeout=10)
