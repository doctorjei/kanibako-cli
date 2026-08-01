"""Integration tests for container operations.

Tests exercise real container runtimes (podman/docker) when available.
Run with::

    pytest -m integration tests/test_container_integration.py -v
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.conftest_integration import requires_runtime


@pytest.mark.integration
class TestRuntimeDetection:
    """Verify real runtime detection on the host."""

    @requires_runtime
    def test_detect_finds_podman_or_docker(self):
        """Real ``shutil.which`` finds a container runtime."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        assert rt.cmd is not None
        assert Path(rt.cmd).name in ("podman", "docker") or os.path.isfile(rt.cmd)

    def test_env_override_takes_precedence(self, monkeypatch):
        """``KANIBAKO_DOCKER_CMD`` overrides automatic detection."""
        from kanibako.runtime.container import ContainerRuntime

        monkeypatch.setenv("KANIBAKO_DOCKER_CMD", "/usr/bin/true")
        rt = ContainerRuntime()
        assert rt.cmd == "/usr/bin/true"


@pytest.mark.integration
class TestImageOperations:
    """Image inspect / pull against a real registry."""

    @requires_runtime
    def test_image_exists_returns_true_for_pulled_image(self, pulled_image):
        """``image inspect`` succeeds for a locally-pulled image."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        assert rt.image_exists(pulled_image) is True

    @requires_runtime
    def test_image_exists_returns_false_for_missing(self, container_runtime_cmd):
        """``image inspect`` fails for a bogus image tag."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        assert rt.image_exists("nonexistent-xyz-image:latest") is False

    @requires_runtime
    def test_pull_succeeds_for_real_image(self, container_runtime_cmd):
        """Real registry pull of a lightweight image returns True."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        assert rt.pull("busybox:latest") is True

    @requires_runtime
    def test_pull_fails_for_nonexistent_image(self, container_runtime_cmd):
        """Pull of a bogus image returns False."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        assert rt.pull("nonexistent-registry.example.com/x:latest") is False

    @requires_runtime
    def test_ensure_image_skips_pull_when_exists(self, pulled_image):
        """No-op when image is already present locally."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()
        # Should not raise — image already present
        rt.ensure_image(pulled_image, Path("/nonexistent"))


@pytest.mark.integration
class TestRunContainer:
    """Run real containers via podman/docker."""

    @requires_runtime
    def test_run_returns_zero_on_success(self, pulled_image, container_runtime_cmd):
        """``/bin/true`` inside the container exits 0."""
        result = subprocess.run(
            [container_runtime_cmd, "run", "--rm", pulled_image, "/bin/true"],
            capture_output=True,
        )
        assert result.returncode == 0

    @requires_runtime
    def test_run_returns_nonzero_on_failure(self, pulled_image, container_runtime_cmd):
        """``/bin/false`` inside the container exits != 0."""
        result = subprocess.run(
            [container_runtime_cmd, "run", "--rm", pulled_image, "/bin/false"],
            capture_output=True,
        )
        assert result.returncode != 0

    @requires_runtime
    def test_run_volume_mounts_are_accessible(self, pulled_image, container_runtime_cmd):
        """A host file is readable inside the container via volume mount."""
        with tempfile.TemporaryDirectory() as tmpdir:
            host_file = Path(tmpdir) / "testfile.txt"
            host_file.write_text("hello from host")

            result = subprocess.run(
                [
                    container_runtime_cmd, "run", "--rm",
                    "-v", f"{tmpdir}:/mnt/testdir:ro",
                    pulled_image,
                    "cat", "/mnt/testdir/testfile.txt",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert result.stdout.strip() == "hello from host"


@pytest.mark.integration
class TestListLocalImages:
    """Verify real ``images`` output parsing."""

    @requires_runtime
    def test_list_includes_pulled_image(self, pulled_image, container_runtime_cmd):
        """Parsed output contains the pulled image when tagged with kanibako prefix."""
        from kanibako.runtime.container import ContainerRuntime

        rt = ContainerRuntime()

        # Tag busybox with a kanibako prefix so list_local_images picks it up
        subprocess.run(
            [container_runtime_cmd, "tag", pulled_image, "kanibako-test:latest"],
            capture_output=True,
            check=True,
        )

        try:
            images = rt.list_local_images()
            repos = [repo for repo, _size in images]
            assert any("kanibako-test" in r for r in repos), (
                f"kanibako-test not found in {repos}"
            )
        finally:
            subprocess.run(
                [container_runtime_cmd, "rmi", "kanibako-test:latest"],
                capture_output=True,
            )
