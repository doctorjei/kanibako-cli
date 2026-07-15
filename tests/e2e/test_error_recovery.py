"""E2E tests for error recovery: container death, auto-retry."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    SUBPROCESS_TIMEOUT,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


def _container_exists(name: str) -> bool:
    """True iff a container named *name* exists (any state), via ``podman ps -a``."""
    podman = shutil.which("podman")
    assert podman is not None, "podman required"
    out = subprocess.run(
        [podman, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return name in {line.strip() for line in out.stdout.strip().splitlines()}


class TestContainerDeath:
    """Test 9: Agent dies immediately → crash fails cleanly, no raw exec error."""

    def test_death_fails_cleanly_without_exec_error(self, e2e_env):
        """An immediately-exiting agent yields rc != 0, no raw error, box gone."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-death"],
            env=env,
        )
        assert result.returncode == 0

        # Start with error stub — agent dies immediately
        result = run_kanibako(
            ["start", "e2e-death",
             "-e", "CLAUDE_STUB_MODE=error",
             "-e", "CLAUDE_STUB_STDERR=agent-crashed-with-error-42"],
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )

        # Should fail
        assert result.returncode != 0

        # NOTE (contract change, 07-14b arc): the crash-output assertion
        # (``"agent-crashed-with-error-42" in result.stderr``) was REMOVED.
        # The sole-agent teardown path no longer arms tmux remain-on-exit, so
        # the pane CLOSES on exit and there is no dead-pane capture to surface
        # — crash-output surfacing is intentionally deferred to the pipe-pane
        # follow-up (tasks.md 07-14b).  RESTORE that assertion when it lands.

        # stderr should NOT contain the raw podman exec error
        assert "container state improper" not in result.stderr, (
            f"Got raw podman error leaking to the user:\n{result.stderr}"
        )

        # Prompt teardown: the crashed box's container is GONE afterwards (the
        # two-state lifecycle tears an exited box down; nothing lingers).
        assert not _container_exists("kanibako-e2e-death"), (
            "Expected the crashed box's container to be torn down after the "
            "failed start, but 'kanibako-e2e-death' still exists."
        )

# NOTE: the former ``TestNoConversationRetry`` (Test 10) was REMOVED with the
# launch-time crash-and-retry net.  The continue-vs-fresh decision is now made
# UP FRONT (``Target.has_resumable_session``): a fresh box goes straight to a
# new session and never hits the "No conversation found" crash, so there is
# nothing to retry.  A replacement e2e (fresh box -> new-session launch, no
# crash, no retry message) should be authored during bifrost validation.
