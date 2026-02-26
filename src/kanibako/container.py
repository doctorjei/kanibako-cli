"""ContainerRuntime: detect podman/docker, pull/build/run images, list images."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.containerfiles import get_containerfile
from kanibako.errors import ContainerError
from kanibako.log import get_logger

logger = get_logger("container")


# Map image name patterns to Containerfile suffixes.
_IMAGE_CONTAINERFILE_MAP = {
    "kanibako-base": "base",
    "kanibako:latest": "base",
    "kanibako-systems": "systems",
    "kanibako-jvm": "jvm",
    "kanibako-android": "android",
    "kanibako-ndk": "ndk",
    "kanibako-dotnet": "dotnet",
    "kanibako-behemoth": "behemoth",
}


class ContainerRuntime:
    """Wrapper around podman/docker CLI."""

    def __init__(self, command: str | None = None) -> None:
        if command:
            self.cmd = command
        else:
            self.cmd = self._detect()

    @staticmethod
    def _detect() -> str:
        env = os.environ.get("KANIBAKO_DOCKER_CMD")
        if env:
            return env
        for name in ("podman", "docker"):
            path = shutil.which(name)
            if path:
                return path
        raise ContainerError(
            "No container runtime found. Install podman or docker."
        )

    # ------------------------------------------------------------------
    # Image operations
    # ------------------------------------------------------------------

    def image_exists(self, image: str) -> bool:
        result = subprocess.run(
            [self.cmd, "image", "inspect", image],
            capture_output=True,
        )
        return result.returncode == 0

    def pull(self, image: str, *, quiet: bool = True) -> bool:
        """Pull *image* from registry. Returns True on success."""
        result = subprocess.run(
            [self.cmd, "pull", image],
            capture_output=quiet,
        )
        return result.returncode == 0

    def build(self, image: str, containerfile: Path, context: Path) -> None:
        """Build *image* from *containerfile*. Raises ContainerError on failure."""
        result = subprocess.run(
            [self.cmd, "build", "-t", image, "-f", str(containerfile), str(context)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ContainerError(
                f"Failed to build image {image}:\n{result.stderr}"
            )

    def rebuild(self, image: str, containerfile: Path, context: Path) -> int:
        """Rebuild *image* with --no-cache, streaming output. Returns exit code."""
        result = subprocess.run(
            [
                self.cmd, "build", "--no-cache",
                "-t", image, "-f", str(containerfile), str(context),
            ],
        )
        return result.returncode

    def guess_containerfile(self, image: str) -> str | None:
        """Return the Containerfile suffix for a known image pattern, or None."""
        return self._guess_containerfile(image)

    def ensure_image(self, image: str, containers_dir: Path) -> None:
        """Make sure *image* is available locally: inspect → pull → build fallback."""
        if self.image_exists(image):
            return

        print(
            f"Container image not found locally. Pulling {image}...",
            file=sys.stderr,
        )
        if self.pull(image):
            print("Image pulled successfully.", file=sys.stderr)
            return

        print("Pull failed. Attempting local build...", file=sys.stderr)
        suffix = self._guess_containerfile(image)
        if suffix is None:
            raise ContainerError(
                f"Container image not available and cannot determine Containerfile "
                f"for: {image}"
            )
        containerfile = get_containerfile(suffix, containers_dir)
        if containerfile is None:
            raise ContainerError(
                f"Container image not available and no local Containerfile found.\n"
                f"Image: {image}"
            )
        self.build(image, containerfile, containerfile.parent)
        print("Image built successfully.", file=sys.stderr)

    @staticmethod
    def _guess_containerfile(image: str) -> str | None:
        for pattern, suffix in _IMAGE_CONTAINERFILE_MAP.items():
            if pattern in image:
                return suffix
        return None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self,
        image: str,
        *,
        shell_path: Path,
        project_path: Path,
        vault_ro_path: Path,
        vault_rw_path: Path,
        extra_mounts: list | None = None,
        vault_tmpfs: bool = False,
        vault_enabled: bool = True,
        env: dict[str, str] | None = None,
        name: str | None = None,
        entrypoint: str | None = None,
        cli_args: list[str] | None = None,
        detach: bool = False,
    ) -> int:
        """Run a container and return the exit code.

        When *detach* is True the container runs in the background (``-d``
        instead of ``-it``, no ``--rm``).  Returns 0 on success.
        """
        if detach:
            run_flags = ["-d", "--userns=keep-id"]
        else:
            run_flags = ["-it", "--rm", "--userns=keep-id"]
        cmd: list[str] = [
            self.cmd, "run", *run_flags,
            # Persistent agent home
            "-v", f"{shell_path}:/home/agent:Z,U",
            # Project workspace
            "-v", f"{project_path}:/home/agent/workspace:Z,U",
            "-w", "/home/agent/workspace",
        ]
        # Vault mounts (only if directories exist and vault is enabled)
        if vault_enabled:
            if vault_ro_path.is_dir():
                cmd += ["-v", f"{vault_ro_path}:/home/agent/share-ro:ro"]
            if vault_rw_path.is_dir():
                cmd += ["-v", f"{vault_rw_path}:/home/agent/share-rw:Z,U"]
            # AC vault hiding: read-only tmpfs over workspace/vault
            if vault_tmpfs:
                cmd += ["--mount", "type=tmpfs,dst=/home/agent/workspace/vault,ro"]
        # Extra mounts (target binary mounts, etc.)
        if extra_mounts:
            for mount in extra_mounts:
                cmd += ["-v", mount.to_volume_arg()]
        if env:
            for k, v in sorted(env.items()):
                cmd += ["-e", f"{k}={v}"]
        if name:
            cmd += ["--name", name]
        if entrypoint:
            cmd += ["--entrypoint", entrypoint]
        cmd.append(image)
        if cli_args:
            cmd.extend(cli_args)

        logger.debug("Container command: %s", cmd)

        result = subprocess.run(cmd)
        return result.returncode

    def exec(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> int:
        """Run a command inside a running container. Interactive (inherits stdio).

        Returns the exit code of the exec'd process.
        """
        cmd: list[str] = [self.cmd, "exec", "-it"]
        if env:
            for k, v in sorted(env.items()):
                cmd += ["-e", f"{k}={v}"]
        cmd.append(name)
        cmd.extend(command)

        logger.debug("Container exec: %s", cmd)
        result = subprocess.run(cmd)
        return result.returncode

    def container_exists(self, name: str) -> bool:
        """Check if a container exists (running or stopped)."""
        result = subprocess.run(
            [self.cmd, "inspect", name],
            capture_output=True,
        )
        return result.returncode == 0

    def stop(self, name: str) -> bool:
        """Stop a running container by name. Returns True if stopped."""
        result = subprocess.run(
            [self.cmd, "stop", name],
            capture_output=True,
        )
        return result.returncode == 0

    def rm(self, name: str) -> bool:
        """Remove a stopped container by name. Returns True if removed."""
        result = subprocess.run(
            [self.cmd, "rm", name],
            capture_output=True,
        )
        return result.returncode == 0

    def is_running(self, name: str) -> bool:
        """Check if a named container is currently running."""
        result = subprocess.run(
            [self.cmd, "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def list_running(self, prefix: str = "kanibako-") -> list[tuple[str, str, str]]:
        """Return running containers matching *prefix* as (name, image, status) tuples."""
        result = subprocess.run(
            [
                self.cmd, "ps",
                "--filter", f"name={prefix}",
                "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
        )
        containers: list[tuple[str, str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                containers.append((parts[0], parts[1], parts[2]))
        return containers

    # ------------------------------------------------------------------
    # Digest
    # ------------------------------------------------------------------

    def get_local_digest(self, image: str) -> str | None:
        """Return the repo digest (``sha256:...``) for a local image, or None."""
        try:
            result = subprocess.run(
                [self.cmd, "image", "inspect", image, "--format", "json"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return None
            import json
            data = json.loads(result.stdout)
            # podman returns a list, docker returns an object
            if isinstance(data, list):
                data = data[0] if data else {}
            digests = data.get("RepoDigests", [])
            if not digests:
                return None
            # Extract the sha256:... portion from e.g. "ghcr.io/x/img@sha256:abc..."
            digest = digests[0]
            if "@" in digest:
                return digest.split("@", 1)[1]
            return digest
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_local_images(self) -> list[tuple[str, str]]:
        """Return local kanibako images as (repo:tag, size) tuples."""
        result = subprocess.run(
            [self.cmd, "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"],
            capture_output=True,
            text=True,
        )
        images: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            if "kanibako" in line.lower():
                parts = line.split("\t", 1)
                repo = parts[0]
                size = parts[1] if len(parts) > 1 else ""
                images.append((repo, size))
        return images
