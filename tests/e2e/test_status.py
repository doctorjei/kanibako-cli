"""E2E tests for ``ps`` / ``list`` reflecting running state, and ``rm``.

These exercise the box-status surface against real podman:
- ``ps`` lists active (running) boxes; ``ps -q`` is the name-only form.
- ``rm --purge --force`` removes box metadata state (it does NOT touch
  containers — stop does that).
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


class TestPsReflectsRunningState:
    """``ps`` shows a running box and drops it once stopped."""

    def test_ps_lists_running_then_drops_on_stop(self, e2e_env):
        """ps / ps -q contain the box name while running, not after stop.

        run_list (which ps delegates to) prints the project NAME in both the
        table form (``NAME  STATUS  PATH``) and the quiet form (``-q``: name
        per line), and cross-references running containers via
        ``runtime.list_running()`` to mark a box "active".  We assert on
        substring presence of the unique box name so coexisting e2e boxes
        don't break the test.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        name = "e2e-status"

        result = run_kanibako(
            ["create", str(project), "--name", name],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container(f"kanibako-{name}", timeout=15)

        # ps (table form) should list the active box by name.
        ps_result = run_kanibako(["ps"], env=env)
        assert ps_result.returncode == 0, f"ps failed: {ps_result.stderr}"
        assert name in ps_result.stdout, (
            f"Expected {name!r} in ps output, got:\n{ps_result.stdout}"
        )

        # ps -q (quiet form) should also list the box name.
        psq_result = run_kanibako(["ps", "-q"], env=env)
        assert psq_result.returncode == 0, f"ps -q failed: {psq_result.stderr}"
        assert name in psq_result.stdout, (
            f"Expected {name!r} in ps -q output, got:\n{psq_result.stdout}"
        )

        # Stop the box, then ps should no longer report it as active.
        stop_result = run_kanibako(["stop", name], env=env)
        assert stop_result.returncode == 0, f"stop failed: {stop_result.stderr}"

        ps_after = run_kanibako(["ps"], env=env)
        assert ps_after.returncode == 0, f"ps failed: {ps_after.stderr}"
        assert name not in ps_after.stdout, (
            f"Expected {name!r} gone from ps after stop, got:\n{ps_after.stdout}"
        )


class TestRmLifecycle:
    """``rm --purge --force`` removes box metadata state."""

    def test_rm_purge_removes_box_state(self, e2e_env):
        """create → start → stop → rm --purge --force removes the box dir.

        NOTE on real behavior (box/_parser.py ``run_rm``): ``rm`` unregisters
        the project from names.toml and, with ``--purge``, deletes the
        metadata dir under ``boxes/<name>``.  It does NOT remove the
        container — ``stop`` already stops AND removes it (stop.py
        ``_stop_one`` calls runtime.stop then runtime.rm).  ``--force`` skips
        the purge confirmation prompt so this runs non-interactively.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]
        data_home = e2e_env["data_home"]
        name = "e2e-rm"
        container_name = f"kanibako-{name}"

        result = run_kanibako(
            ["create", str(project), "--name", name],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        run_kanibako(
            ["start", name, "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container(container_name, timeout=15)

        # The box metadata dir should exist after start.
        box_dir = data_home / "kanibako" / "boxes" / name
        assert box_dir.is_dir(), f"Box dir missing after start: {box_dir}"

        # Stop removes the container.
        stop_result = run_kanibako(["stop", name], env=env)
        assert stop_result.returncode == 0, f"stop failed: {stop_result.stderr}"

        # Container should be gone after stop.
        inspect = subprocess.run(
            ["podman", "inspect", container_name],
            capture_output=True,
            timeout=5,
        )
        assert inspect.returncode != 0, (
            "Container should not exist after stop"
        )

        # rm --purge --force removes the box metadata dir non-interactively.
        rm_result = run_kanibako(
            ["rm", name, "--purge", "--force"],
            env=env,
        )
        assert rm_result.returncode == 0, f"rm failed: {rm_result.stderr}"
        assert not box_dir.exists(), (
            f"Box dir should be gone after rm --purge: {box_dir}"
        )

        # And the container is still absent.
        inspect2 = subprocess.run(
            ["podman", "inspect", container_name],
            capture_output=True,
            timeout=5,
        )
        assert inspect2.returncode != 0, (
            "Container should not exist after rm"
        )
