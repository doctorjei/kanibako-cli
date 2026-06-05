"""E2E test for Increment 2 lazy-prep: ``start <proj> --rig <template>``.

A single end-to-end test proving the lazy-prep wiring works against real
podman: ``kanibako start <proj> --rig jvm`` resolves the bare template name
``jvm`` via ``resolve_rig``, finds the ``kanibako-template-jvm`` image is
absent, BUILDS it on the fly, then launches a box from it. This is the
end-to-end proof of Increment 2 (box-start lazy-build of a bare template name
through the new ``--rig`` alias).

The critical property under test is that the *start* path builds the template
-- the test deletes ``kanibako-template-jvm`` first (best effort) so the only
way the image can exist at the assertion point is if ``start`` built it.

Like ``test_template_create.py``, this exercises only ``jvm`` as a
representative smoke. The jvm template does ``FROM ...kanibako-oci:latest``;
the session-scoped ``ensure_image_in_pinned_store`` fixture (conftest)
pre-warms that base into the pinned store so the FROM resolves inside the
subprocess. We run WITHOUT ``--base`` and let the template's declared base
apply.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.e2e.conftest import (
    e2e_requires,
    run_kanibako,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

# Resolve podman the same way conftest does (module-level shutil.which).
_podman = shutil.which("podman")

# Lazy-prep builds the jvm template on start: ``apt-get install default-jdk
# kotlin maven`` takes minutes, far beyond conftest's 60s SUBPROCESS_TIMEOUT.
# Give the start-with-build its own generous budget.
BUILD_TIMEOUT = 600  # seconds

# Image the bare template name ``jvm`` resolves to (template_image_name("jvm")).
TEMPLATE_IMAGE = "kanibako-template-jvm"


class TestLazyPrep:
    """``start <proj> --rig jvm`` builds the template, then launches a box."""

    def test_start_rig_template_lazy_builds(self, e2e_env):
        """start --rig jvm → image gets BUILT by the start path, box launches.

        Uses conftest's ``run_kanibako`` helper (same isolated env that pins
        podman storage to the host store) but overrides its timeout to
        BUILD_TIMEOUT, since the lazy template build exceeds the default 60s
        SUBPROCESS_TIMEOUT. ``--ephemeral`` makes the launch non-interactive:
        the claude stub runs once and exits cleanly, so the box actually
        starts (proving build + launch) without hanging on a session.
        """
        env = e2e_env["env"]
        project = e2e_env["project"]

        assert _podman is not None, "podman required"

        # Prove a lazy BUILD: the template image must be ABSENT at the start,
        # so the only way it can exist afterwards is if `start` built it.
        # Best-effort -- ignore failure if it wasn't there to begin with.
        subprocess.run(
            [_podman, "rmi", "-f", TEMPLATE_IMAGE],
            capture_output=True,
            timeout=60,
        )

        try:
            # Register the project (no template here -- the rig is chosen at
            # start time via --rig, which is the lazy-prep path under test).
            result = run_kanibako(
                ["create", str(project), "--name", "e2e-lazyprep"],
                env=env,
            )
            assert result.returncode == 0, (
                f"create failed (rc={result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

            # Start with --rig jvm: resolve_rig sees the bare template name,
            # finds kanibako-template-jvm absent, BUILDS it, then launches.
            # --ephemeral keeps the stub run non-interactive (runs once, exits).
            # Long timeout for the on-the-fly toolchain build.
            result = run_kanibako(
                ["start", "e2e-lazyprep", "--rig", "jvm", "--ephemeral",
                 "-e", "CLAUDE_STUB_MODE=session",
                 "-e", "CLAUDE_STUB_SLEEP=1"],
                env=env,
                timeout=BUILD_TIMEOUT,
            )
            assert result.returncode == 0, (
                f"start --rig jvm failed (rc={result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

            # The template image must now exist locally -- built by the start
            # path, since the test removed it beforehand.
            inspect = subprocess.run(
                [_podman, "image", "inspect", TEMPLATE_IMAGE],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert inspect.returncode == 0, (
                f"{TEMPLATE_IMAGE!r} not found after `start --rig jvm`; "
                f"lazy-prep did not build it: {inspect.stderr}"
            )
        finally:
            # Best-effort cleanup so the suite stays clean; never fail the
            # test on cleanup errors. (e2e_env teardown removes the box's
            # container by the kanibako-e2e- prefix.)
            subprocess.run(
                [_podman, "rmi", "-f", TEMPLATE_IMAGE],
                capture_output=True,
                timeout=60,
            )
