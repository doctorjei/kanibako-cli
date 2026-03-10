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
    "kanibako-min": "kanibako",
    "kanibako-oci": "kanibako",
    "kanibako-lxc": "kanibako",
    "kanibako-vm": "kanibako",
}

# Map image name patterns to build variants (for VARIANT build arg).
_IMAGE_VARIANT_MAP = {
    "kanibako-min": "min",
    "kanibako-oci": "oci",
    "kanibako-lxc": "lxc",
    "kanibako-vm": "vm",
}

# Map image variants to their droste base image for local builds.
_IMAGE_BASE_MAP = {
    "kanibako-min": "ghcr.io/doctorjei/droste-seed:latest",
    "kanibako-oci": "ghcr.io/doctorjei/droste-fiber:latest",
    "kanibako-lxc": "ghcr.io/doctorjei/droste-thread:latest",
    "kanibako-vm": "ghcr.io/doctorjei/droste-hair:latest",
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

    def image_inspect(self, image: str) -> dict | None:
        """Return image metadata as a dict, or None if not found."""
        result = subprocess.run(
            [self.cmd, "image", "inspect", image, "--format", "json"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        import json
        data = json.loads(result.stdout)
        if isinstance(data, list) and data:
            return data[0]
        return data if isinstance(data, dict) else None

    def pull(self, image: str, *, quiet: bool = True) -> bool:
        """Pull *image* from registry. Returns True on success."""
        result = subprocess.run(
            [self.cmd, "pull", image],
            capture_output=quiet,
        )
        return result.returncode == 0

    def remove_image(self, image: str) -> None:
        """Remove a local image. Raises ContainerError on failure."""
        result = subprocess.run(
            [self.cmd, "rmi", image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ContainerError(f"Failed to remove image {image}:\n{result.stderr}")

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

    def rebuild(
        self,
        image: str,
        containerfile: Path,
        context: Path,
        build_args: dict[str, str] | None = None,
    ) -> int:
        """Rebuild *image* with --no-cache, streaming output. Returns exit code."""
        cmd = [self.cmd, "build", "--no-cache", "-t", image, "-f", str(containerfile)]
        if build_args:
            for key, val in build_args.items():
                cmd.extend(["--build-arg", f"{key}={val}"])
        cmd.append(str(context))
        result = subprocess.run(cmd)
        return result.returncode

    @staticmethod
    def get_base_image(image: str) -> str | None:
        """Return the droste base image for a kanibako variant, or None."""
        for pattern, base in _IMAGE_BASE_MAP.items():
            if pattern in image:
                return base
        return None

    @staticmethod
    def get_variant(image: str) -> str | None:
        """Return the build variant (min/oci/lxc/vm) for a kanibako image, or None."""
        for pattern, variant in _IMAGE_VARIANT_MAP.items():
            if pattern in image:
                return variant
        return None

    def run_interactive(self, image: str, *, container_name: str | None = None) -> int:
        """Run an interactive container. Returns exit code."""
        cmd = [self.cmd, "run", "-it"]
        if container_name:
            cmd.extend(["--name", container_name])
        cmd.append(image)
        result = subprocess.run(cmd)
        return result.returncode

    def commit(self, container: str, image: str) -> None:
        """Commit a container to a new image. Raises ContainerError on failure."""
        result = subprocess.run(
            [self.cmd, "commit", container, image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ContainerError(f"Failed to commit container: {result.stderr}")

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
        # Pre-create mount destination stubs so crun doesn't need to mkdir
        # inside bind-mounted overlay filesystems (fails in LXC).
        _precreate_mount_stubs(
            shell_path, project_path, extra_mounts,
            vault_enabled, vault_ro_path, vault_rw_path, vault_tmpfs,
        )

        if detach:
            run_flags = ["-dt", "--userns=keep-id"]
        else:
            tty_flag = "-it" if sys.stdin.isatty() else "-i"
            run_flags = [tty_flag, "--rm", "--userns=keep-id"]
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
            # Local vault hiding: read-only tmpfs over workspace/vault
            if vault_tmpfs:
                cmd += ["--mount", "type=tmpfs,dst=/home/agent/workspace/vault,ro"]
                # Mount a .gitignore on top of the tmpfs so the stub
                # directories created by the OCI runtime are ignored.
                import importlib.resources
                gi_ref = importlib.resources.files("kanibako.scripts").joinpath("vault-gitignore")
                gi_path = Path(str(gi_ref))
                if gi_path.is_file():
                    cmd += ["-v", f"{gi_path}:/home/agent/workspace/vault/.gitignore:ro"]
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

    def list_all(self, prefix: str = "kanibako-") -> list[tuple[str, str, str]]:
        """Return all containers (running + stopped) matching *prefix*.

        Returns (name, image, status) tuples.
        """
        result = subprocess.run(
            [
                self.cmd, "ps", "-a",
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


def _precreate_mount_stubs(
    shell_path: Path,
    project_path: Path,
    extra_mounts: list | None,
    vault_enabled: bool,
    vault_ro_path: Path,
    vault_rw_path: Path,
    vault_tmpfs: bool,
) -> None:
    """Pre-create mount destination stubs to avoid crun permission errors.

    In some environments (e.g. LXC nested containers), the OCI runtime
    cannot create mount-point directories inside bind-mounted overlay
    filesystems.  Pre-creating the stubs on the host side avoids the
    problem.

    Mapping: destinations under ``/home/agent/workspace/`` are created
    relative to *project_path*; other destinations under ``/home/agent/``
    are created relative to *shell_path*.
    """
    AGENT_HOME = "/home/agent/"
    WORKSPACE = "/home/agent/workspace/"

    def _ensure_dir(p: Path) -> None:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _ensure_file(p: Path) -> None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.touch()
        except OSError:
            pass

    # Built-in directory mounts.
    _ensure_dir(shell_path / "workspace")
    if vault_enabled:
        if vault_ro_path.is_dir():
            _ensure_dir(shell_path / "share-ro")
        if vault_rw_path.is_dir():
            _ensure_dir(shell_path / "share-rw")
        if vault_tmpfs:
            _ensure_dir(project_path / "vault")

    # Extra mounts: pre-create destination stubs.
    if not extra_mounts:
        return
    for mount in extra_mounts:
        dest = mount.destination
        if dest.startswith(WORKSPACE):
            rel = dest[len(WORKSPACE):]
            host_path = project_path / rel
        elif dest.startswith(AGENT_HOME):
            rel = dest[len(AGENT_HOME):]
            host_path = shell_path / rel
        else:
            continue

        if mount.source.is_dir():
            _ensure_dir(host_path)
        else:
            _ensure_file(host_path)
