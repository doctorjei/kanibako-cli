"""E2E test for the ``rig create --template`` CLI path.

A single end-to-end test proving the template-build wiring works against
real podman: discovery → ``runtime.rebuild`` → image built → toolchain runs.
This is NOT a per-template matrix; it exercises only ``jvm`` as a
representative CLI-wiring smoke.

The jvm template's Containerfile does ``FROM ...kanibako-oci:latest``; the
session-scoped ``ensure_image_in_pinned_store`` fixture (conftest) pre-warms
that image into the pinned store, so the FROM resolves inside the subprocess.
We therefore run WITHOUT ``--base`` and let the template's declared base apply.
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

# A template build runs ``apt-get install default-jdk kotlin maven``, which
# takes minutes — far beyond conftest's 60s SUBPROCESS_TIMEOUT. Give the build
# its own generous budget.
BUILD_TIMEOUT = 600  # seconds


class TestTemplateCreate:
    """``rig create --template jvm`` builds a working JVM toolchain image."""

    def test_rig_create_template_jvm(self, e2e_env):
        """create --template jvm → image exists and ``java -version`` runs.

        Uses conftest's ``run_kanibako`` helper (same isolated env that pins
        podman storage to the host store) but overrides its timeout to
        BUILD_TIMEOUT, since the apt-get toolchain install exceeds the default
        60s SUBPROCESS_TIMEOUT.
        """
        env = e2e_env["env"]
        image_name = "kanibako-template-e2e-jvm"

        assert _podman is not None, "podman required"

        try:
            # 1. Build the template image via the real CLI (no --base: use the
            #    template's declared base, which the pre-warmed oci image
            #    satisfies). Long timeout for the toolchain install.
            result = run_kanibako(
                ["rig", "create", "e2e-jvm", "--template", "jvm"],
                env=env,
                timeout=BUILD_TIMEOUT,
            )
            assert result.returncode == 0, (
                f"rig create --template jvm failed (rc={result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

            # 2. The template image should now exist locally.
            inspect = subprocess.run(
                [_podman, "image", "inspect", image_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert inspect.returncode == 0, (
                f"image {image_name!r} not found after build: {inspect.stderr}"
            )

            # 3. The JVM toolchain actually runs inside the built image.
            java = subprocess.run(
                [_podman, "run", "--rm", image_name, "sh", "-lc",
                 "java -version"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert java.returncode == 0, (
                f"'java -version' failed in {image_name!r} "
                f"(rc={java.returncode}):\nstdout:\n{java.stdout}\n"
                f"stderr:\n{java.stderr}"
            )
        finally:
            # Best-effort cleanup so the suite stays clean; never fail the
            # test on cleanup errors.
            subprocess.run(
                [_podman, "rmi", "-f", image_name],
                capture_output=True,
                timeout=60,
            )
