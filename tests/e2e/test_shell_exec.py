"""E2E test: shell execs into the already-running container.

Guards the "exec into running" fix (commit bbaf02d): when a persistent
container is already running for a project, ``kanibako shell <name> -- cmd``
must exec into THAT container rather than erroring or recreating it.
See start.py ``_run_container``: the non-persistent branch checks
``runtime.is_running(container_name) and entrypoint is not None`` and calls
``runtime.exec(container_name, exec_cmd)`` directly.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    wait_for_container,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


class TestShellExecIntoRunning:
    """shell against a live container execs in; it does not recreate."""

    def test_shell_execs_into_existing_container(self, e2e_env):
        """shell -- echo runs inside the live container; Id is unchanged."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-exec"],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # Start a persistent, long-running container to exec into.
        run_kanibako(
            ["start", "e2e-exec",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        container_name = "kanibako-e2e-exec"
        wait_for_container(container_name, timeout=15)

        # Capture the container Id before exec.
        id_before = subprocess.run(
            ["podman", "inspect", "--format", "{{.Id}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        assert id_before, "Could not read container Id before exec"

        # Exec a one-shot command via shell, carrying a per-run -e var.
        # Guards that -e propagates on the exec-into-running path (it was
        # previously dropped because the early exec branch never built the
        # container env). printenv must echo the value we passed.
        result = run_kanibako(
            ["shell", "e2e-exec",
             "-e", "KANIBAKO_E2E_EXEC_MARKER=exec-marker-77",
             "--", "printenv", "KANIBAKO_E2E_EXEC_MARKER"],
            env=env,
        )
        assert result.returncode == 0, f"shell exec failed: {result.stderr}"
        assert "exec-marker-77" in result.stdout, (
            f"Expected per-run -e var in exec output, got: {result.stdout!r}"
        )

        # The container Id must be unchanged — it exec'd in, did not recreate.
        id_after = subprocess.run(
            ["podman", "inspect", "--format", "{{.Id}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        assert id_after == id_before, (
            f"Container Id changed (recreated?): {id_before} -> {id_after}"
        )

        # Exactly one container with this name should exist.
        ps_result = subprocess.run(
            ["podman", "ps", "-a",
             "--filter", f"name={container_name}",
             "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        names = [
            n.strip()
            for n in ps_result.stdout.strip().splitlines()
            if n.strip()
        ]
        assert names == [container_name], (
            f"Expected exactly one {container_name}, got: {names}"
        )
