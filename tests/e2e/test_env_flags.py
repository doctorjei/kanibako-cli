"""E2E tests for per-run env injection and flag-after-positional parsing.

These guard the #79 REMAINDER fix: infrastructure flags (``-e``, ``--ephemeral``)
that follow the project positional must be parsed by kanibako and applied to the
container, not silently forwarded to the agent/shell command.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    SUBPROCESS_TIMEOUT,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


class TestEnvForwarding:
    """``-e KEY=VALUE`` reaches the container environment."""

    def test_shell_ephemeral_forwards_env(self, e2e_env):
        """shell --ephemeral -e KEY=VAL -- printenv KEY prints the value.

        Uses a *fresh* ephemeral shell container (not an exec into an
        already-running one).  The fresh-launch path merges ``-e`` vars into
        the container env (start.py ``_run_container`` cli_env handling) and
        passes them via ``podman run -e``.  ``shell`` wraps the post-``--``
        args as ``/bin/sh -c "<joined args>"``, so ``printenv`` runs inside
        the container and writes to the foreground stdout we capture here.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-env-fwd"],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        # -e and --ephemeral both placed AFTER the project positional.
        result = run_kanibako(
            ["shell", "e2e-env-fwd", "--ephemeral",
             "-e", "KANIBAKO_E2E_MARKER=marker-xyz-123",
             "--", "printenv", "KANIBAKO_E2E_MARKER"],
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )
        assert result.returncode == 0, (
            f"shell failed (rc={result.returncode}): {result.stderr}"
        )
        assert "marker-xyz-123" in result.stdout, (
            f"Expected forwarded env value in stdout, got: {result.stdout!r}"
        )


class TestFlagAfterPositional:
    """Infrastructure flags after the positional are parsed, not forwarded."""

    def test_ephemeral_after_env_flags_is_honored(self, e2e_env):
        """start <name> -e ... --ephemeral runs the stub once and exits 0.

        ``--ephemeral`` appears AFTER both ``-e`` flags and the project
        positional.  If it were forwarded to the agent (the pre-#79 REMAINDER
        bug) the run would launch persistently and the subprocess would block
        on tmux attach instead of returning.  Returning 0 with the stub banner
        in stdout proves ``--ephemeral`` was parsed by kanibako (ephemeral =
        run in foreground, run-and-exit).
        """
        env = e2e_env["env"]
        project = e2e_env["project"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-flag-order"],
            env=env,
        )
        assert result.returncode == 0, f"create failed: {result.stderr}"

        result = run_kanibako(
            ["start", "e2e-flag-order",
             "-e", "CLAUDE_STUB_MODE=session",
             "-e", "CLAUDE_STUB_SLEEP=1",
             "--ephemeral"],
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )
        assert result.returncode == 0, (
            f"ephemeral start failed (rc={result.returncode}): {result.stderr}"
        )
        assert "claude-stub" in result.stdout, (
            f"Expected stub banner in stdout, got: {result.stdout!r}"
        )
