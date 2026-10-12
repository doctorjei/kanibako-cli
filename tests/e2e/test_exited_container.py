"""E2E: a box whose container outlived its process (``Exited``) or cannot be stopped (``Paused``).

A detached box runs without ``--rm``, so when its process exits the container stays,
``Exited``, holding the box's name.  ``box ps``, ``shell``, and ``stop`` each meet it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.e2e.conftest import (
    box_container,
    e2e_requires,
    run_kanibako,
    wait_for_container,
    SUBPROCESS_TIMEOUT,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


def _podman(*args: str) -> subprocess.CompletedProcess[str]:
    podman = shutil.which("podman")
    assert podman is not None, "podman required"
    return subprocess.run([podman, *args], capture_output=True, text=True, timeout=30)


def _status(name: str) -> str | None:
    """The container's ``.State.Status`` (``running``, ``exited``, ``paused``), or None if absent."""
    r = _podman("inspect", "--format", "{{.State.Status}}", name)
    return r.stdout.strip() if r.returncode == 0 else None


def _start_detached(env: dict[str, str], project: str, box: str) -> str:
    """Create *box* at *project*, start it detached, and return its running container's name."""
    r = run_kanibako(["create", project, "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = run_kanibako(
        ["start", box, "--detach", "--no-auto-auth", "-e", "CLAUDE_STUB_MODE=long-running"],
        env=env, timeout=SUBPROCESS_TIMEOUT,
    )
    assert r.returncode == 0, f"start --detach failed: {r.stdout}{r.stderr}"
    name = box_container(box)
    wait_for_container(name, timeout=15)
    return name


class TestExitedContainer:
    """The container exists, ``Exited``; the box is not running."""

    def test_ps_shell_stop_on_exited_container(self, e2e_env):
        env = e2e_env["env"]
        name = _start_detached(env, str(e2e_env["project"]), "e2e_exited")
        try:
            r = _podman("stop", "-t", "3", name)
            assert r.returncode == 0, r.stderr
            assert _status(name) == "exited"

            # box ps: not active.  box ps --all: listed, stopped.
            r = run_kanibako(["box", "ps"], env=env)
            assert r.returncode == 0, r.stderr
            assert "e2e_exited" not in r.stdout
            r = run_kanibako(["box", "ps", "--all"], env=env)
            assert r.returncode == 0, r.stderr
            row = [line for line in r.stdout.splitlines() if line.startswith("e2e_exited")]
            assert len(row) == 1 and "stopped" in row[0], r.stdout

            # box info names the exited container and its cure.
            r = run_kanibako(["box", "info", "e2e_exited"], env=env)
            assert r.returncode == 0, r.stderr
            assert f"exited, still holding the name ({name})" in r.stdout
            assert "kanibako stop e2e_exited" in r.stdout

            # shell: refused, naming the blocker and the cure; the container is untouched.
            r = run_kanibako(["shell", "e2e_exited", "--", "true"], env=env, timeout=SUBPROCESS_TIMEOUT)
            assert r.returncode == 1, f"rc={r.returncode}: {r.stdout}{r.stderr}"
            assert "stopped container is still holding its name" in r.stderr
            assert name in r.stderr and "kanibako stop e2e_exited" in r.stderr
            assert "container state improper" not in r.stdout + r.stderr
            assert _status(name) == "exited"

            # stop: removes it and says so — not "Stopped", the box was not live.
            r = run_kanibako(["stop", "e2e_exited"], env=env)
            assert r.returncode == 0, r.stderr
            assert f"Removed stopped container: {name}" in r.stdout
            assert f"Stopped {name}" not in r.stdout
            assert _status(name) is None

            # A second stop finds nothing, and says so.
            r = run_kanibako(["stop", "e2e_exited"], env=env)
            assert r.returncode == 0, r.stderr
            assert f"No running container found for this project ({name})" in r.stdout
            assert "Removed" not in r.stdout
        finally:
            _podman("rm", "-f", name)


class TestUnstoppableContainer:
    """``podman stop`` fails while the container still exists: a ``Paused`` container (rc 125)."""

    def test_stop_on_paused_container_unpauses_stops_and_removes_it(self, e2e_env):
        env = e2e_env["env"]
        name = _start_detached(env, str(e2e_env["project"]), "e2e_paused")
        try:
            r = _podman("pause", name)
            assert r.returncode == 0, r.stderr
            assert _status(name) == "paused"
            assert _podman("stop", "-t", "3", name).returncode != 0, "podman stop must fail on a paused container"
            assert _status(name) == "paused"

            # stop unpauses the container, stops it, and removes it.
            r = run_kanibako(["stop", "e2e_paused"], env=env)
            assert r.returncode == 0, r.stdout + r.stderr
            assert _status(name) is None, r.stdout + r.stderr
        finally:
            _podman("unpause", name)
            _podman("rm", "-f", name)
