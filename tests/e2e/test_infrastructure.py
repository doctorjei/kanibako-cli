"""E2E infrastructure tests: mount stubs, cleanup."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
    wait_for_container,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]


class TestMountStubs:
    """Test 11: Mount point directories are pre-created in shell dir."""

    def test_mount_stubs_created_after_start(self, e2e_env):
        """After start, shell dir contains all expected mount point stubs."""
        env = e2e_env["env"]
        project = e2e_env["project"]
        data_home = e2e_env["data_home"]

        result = run_kanibako(
            ["create", str(project), "--name", "e2e-stubs"],
            env=env,
        )
        assert result.returncode == 0

        # Start a container so mount stubs get created
        run_kanibako(
            ["start", "e2e-stubs",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env,
        )
        wait_for_container("kanibako-e2e-stubs", timeout=15)

        # Find the shell directory
        shells_dir = data_home / "kanibako" / "shells"
        shell_dirs = list(shells_dir.iterdir()) if shells_dir.exists() else []
        assert len(shell_dirs) > 0, (
            f"No shell directories found under {shells_dir}"
        )

        shell_path = shell_dirs[0]

        # These directories must exist as mount point stubs.
        # Without them, crun fails with "Permission denied" in LXC (#57).
        # "workspace" and "comms" come from _precreate_mount_stubs(),
        # ".claude" comes from target.init_home().
        expected_stubs = [
            "workspace",
            ".claude",
            "comms",
        ]
        for stub in expected_stubs:
            stub_path = shell_path / stub
            assert stub_path.exists(), (
                f"Mount stub missing: {stub_path} "
                f"(contents: {list(shell_path.iterdir())})"
            )
