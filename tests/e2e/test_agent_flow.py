"""E2E tests for agent-specific flow: entrypoint, credentials, error recovery."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    podman_exec,
    run_kanibako,
    wait_for_container,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


def _claude_plugin_available() -> bool:
    """Check if the kanibako-agent-claude plugin is importable."""
    try:
        import kanibako.plugins.claude  # noqa: F401
        return True
    except ImportError:
        return False


requires_claude_plugin = pytest.mark.skipif(
    not _claude_plugin_available(),
    reason="kanibako-agent-claude plugin not installed",
)


class TestEntrypoint:
    """Test 7: Container runs the agent stub, not bare /bin/bash."""

    def test_process_tree_shows_stub(self, e2e_env):
        """The agent entrypoint is the stub, not /bin/bash."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-entrypoint"],
            env=env,
        )
        assert result.returncode == 0

        run_kanibako(
            ["start", "e2e-entrypoint",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-entrypoint", timeout=15)

        # Check process tree inside container
        ps_result = podman_exec(
            "kanibako-e2e-entrypoint", ["ps", "aux"]
        )
        assert ps_result.returncode == 0

        # In persistent mode, tmux wraps the agent. The process tree
        # should contain "claude" (the stub binary name) somewhere.
        # It should NOT be just /bin/bash with nothing else.
        procs = ps_result.stdout
        has_agent = "claude" in procs
        only_bash = (
            "bash" in procs
            and not has_agent
        )
        assert has_agent and not only_bash, (
            f"Expected claude in process tree, got:\n{procs}"
        )


@requires_claude_plugin
class TestCredentials:
    """Test 8: Host credentials are forwarded into the container."""

    def test_credentials_file_exists_in_container(self, e2e_env):
        """Seeded credentials are accessible inside the container."""
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-creds"],
            env=env,
        )
        assert result.returncode == 0

        run_kanibako(
            ["start", "e2e-creds",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-creds", timeout=15)

        # Check that credential file exists inside the container
        result = podman_exec(
            "kanibako-e2e-creds",
            ["test", "-f", "/home/agent/.claude/.credentials.json"],
        )
        assert result.returncode == 0, (
            "Credential file not found inside container"
        )

        # Verify it contains our test token
        cat_result = podman_exec(
            "kanibako-e2e-creds",
            ["cat", "/home/agent/.claude/.credentials.json"],
        )
        assert "e2e-test-token" in cat_result.stdout
