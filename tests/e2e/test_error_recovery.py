"""E2E tests for error recovery: container death, auto-retry."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    SUBPROCESS_TIMEOUT,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


class TestContainerDeath:
    """Test 9: Agent dies immediately → user sees logs, not exec error."""

    def test_death_shows_logs_not_exec_error(self, e2e_env):
        """When agent exits immediately, stderr contains agent output."""
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

        # stderr should contain the stub's error output
        assert "agent-crashed-with-error-42" in result.stderr, (
            f"Expected stub error in stderr, got:\n{result.stderr}"
        )

        # stderr should NOT contain the raw podman exec error
        assert "container state improper" not in result.stderr, (
            f"Got raw podman error instead of agent logs:\n{result.stderr}"
        )

# NOTE: the former ``TestNoConversationRetry`` (Test 10) was REMOVED with the
# launch-time crash-and-retry net.  The continue-vs-fresh decision is now made
# UP FRONT (``Target.has_resumable_session``): a fresh box goes straight to a
# new session and never hits the "No conversation found" crash, so there is
# nothing to retry.  A replacement e2e (fresh box -> new-session launch, no
# crash, no retry message) should be authored during bifrost validation.
