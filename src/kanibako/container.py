"""ContainerRuntime: detect podman/docker, pull/build/run images, list images."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from kanibako.errors import ContainerError
from kanibako.log import get_logger
from kanibako.settings_resolve import GUEST_HOME

logger = get_logger("container")


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
            "No container runtime found. "
            "Install podman (https://podman.io/) or Docker."
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
            raise ContainerError(f"Failed to remove rig {image}:\n{result.stderr}")

    def unshare_rm(self, path: Path) -> bool:
        """Remove *path* from within the rootless user namespace.

        Files a ``--userns=keep-id`` container creates as root map to subuids
        the host user cannot ``unlink`` directly, so a plain ``rmtree`` of a
        box's shell dir can fail with EACCES. ``podman unshare`` runs ``rm``
        inside the user namespace where those subuids appear as root, so the
        removal succeeds. Returns True on success. Only podman supports
        ``unshare``; returns False for docker or on any failure.
        """
        if "podman" not in Path(self.cmd).name:
            return False
        result = subprocess.run(
            [self.cmd, "unshare", "rm", "-rf", str(path)],
            capture_output=True,
            text=True,
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
                f"Failed to build rig {image}:\n{result.stderr}"
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

    def cp(self, src: Path, dest: str) -> bool:
        """Copy *src* into a container at *dest* (``<container>:<path>``).

        Returns True on success.
        """
        result = subprocess.run(
            [self.cmd, "cp", str(src), dest],
            capture_output=True,
        )
        return result.returncode == 0

    def save(self, image: str, out: Path) -> bool:
        """Save *image* to a tar archive at *out*. Returns True on success."""
        result = subprocess.run(
            [self.cmd, "save", "-o", str(out), image],
            capture_output=True,
        )
        return result.returncode == 0

    def load(self, archive: Path) -> str | None:
        """Load an image from the tar *archive*.

        Returns the loaded image reference parsed from the runtime's
        ``Loaded image: <ref>`` output (an archive with no RepoTags yields an
        empty string), or ``None`` if the load command itself failed. Reading
        the ref back from the runtime is authoritative -- the archive's
        filename is not a reliable source for the loaded tag.
        """
        result = subprocess.run(
            [self.cmd, "load", "-i", str(archive)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        # podman/docker print e.g. "Loaded image: repo:tag",
        # "Loaded image(s): repo:tag", or "Loaded image ID: sha256:...".
        for line in result.stdout.splitlines():
            m = re.search(r"Loaded image(?:\(s\)| ID)?:\s*(\S.*)$", line)
            if m:
                return m.group(1).strip()
        return ""

    def diff(self, image: str) -> list[str]:
        """Return the changed paths for *image* as verbatim lines.

        Each line is a changed path, possibly prefixed by a change-type
        letter (``C``/``A``/``D``). Returns an empty list on failure.
        """
        result = subprocess.run(
            [self.cmd, "diff", image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def ensure_image(self, image: str, containers_dir: Path | None = None) -> None:
        """Make sure *image* is available locally: inspect, then pull.

        Base images are pull-only -- the cli no longer bundles or builds a base
        Containerfile. On pull failure raise an actionable :class:`ContainerError`
        directing the user to build a custom base themselves. *containers_dir* is
        accepted for call-site compatibility but unused.
        """
        if self.image_exists(image):
            return

        print(
            f"Rig not found locally. Pulling {image}...",
            file=sys.stderr,
            flush=True,
        )
        if self.pull(image, quiet=False):
            print("Rig pulled successfully.", file=sys.stderr)
            return

        raise ContainerError(
            f"Failed to pull rig '{image}'.\n"
            "Check your network/registry access. To use a custom base image, build it\n"
            "yourself (see github.com/doctorjei/kanibako-images) and pass it via --image\n"
            "or set box_image in your config."
        )

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
        tmpfs_masks: list[str] | None = None,
        enable_vault: bool = True,
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
        masks = tmpfs_masks or []
        # Pre-create mount destination stubs so crun doesn't need to mkdir
        # inside bind-mounted overlay filesystems (fails in LXC).
        _precreate_mount_stubs(
            shell_path, project_path, extra_mounts,
            enable_vault, vault_ro_path, vault_rw_path, masks,
        )

        if detach:
            run_flags = ["-dt", "--userns=keep-id"]
        else:
            tty_flag = "-it" if sys.stdin.isatty() else "-i"
            run_flags = [tty_flag, "--rm", "--userns=keep-id"]
        cmd: list[str] = [
            self.cmd, "run", *run_flags,
            # Working directory inside the box.  The home + workspace + vault binds
            # are NO LONGER hardwired here — they flow in via *extra_mounts* (the
            # core box mounts the caller routes through the category resolver,
            # ``start._build_core_mounts``), so nothing is bound into a box except
            # through the keyspace.  Only ``-w`` (a flag, not a mount) stays.
            "-w", f"{GUEST_HOME}/workspace",
        ]
        # Local masking is still emitted here (tmpfs has no host source, so it is
        # not a category MOUNT the caller pre-builds): a read-only tmpfs over each
        # box-dest in the ``box.masks`` category (resolved in start.py).  There is
        # NO default mask -- the vault moved out of ``~/workspace`` in 1.6.0, so
        # there is nothing in the workspace to hide.  A box (or any scope) may
        # declare masks via ``box.masks`` / ``<scope>.masks``; an empty list emits
        # no tmpfs masks.  The ``.gitignore`` overlay that used to ride on the
        # vault tmpfs is DROPPED (no special-case overlay).
        if enable_vault:
            for mask in masks:
                cmd += ["--mount", f"type=tmpfs,dst={mask},ro"]
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

        if detach:
            # ``podman run -d`` prints the new container's full SHA id to
            # stdout. The caller reattaches by NAME (runtime.exec), so the id
            # is not needed; capture it to keep it off the user's terminal and
            # surface it only at DEBUG (``-v``). A genuine launch failure must
            # still be reported, so echo captured stderr on a non-zero return.
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(
                    "Detached container launch failed (exit %s)", result.returncode
                )
                if result.stderr:
                    sys.stderr.write(result.stderr)
            else:
                logger.debug(
                    "Detached container started: %s", (result.stdout or "").strip()
                )
            return result.returncode

        # Interactive foreground path: inherit the terminal so the agent /
        # shell (and tmux attach) get the real stdio/tty.
        fg_result = subprocess.run(cmd)
        return fg_result.returncode

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
        # Allocate a pty only when stdin is a real terminal. In scripted /
        # subprocess contexts (CI, e2e tests), -t causes interactive commands
        # like ``tmux attach`` to render but never return.
        tty_flag = "-it" if sys.stdin.isatty() else "-i"
        cmd: list[str] = [self.cmd, "exec", tty_flag]
        if env:
            for k, v in sorted(env.items()):
                cmd += ["-e", f"{k}={v}"]
        cmd.append(name)
        cmd.extend(command)

        logger.debug("Container exec: %s", cmd)
        result = subprocess.run(cmd)
        return result.returncode

    def exec_ready(self, name: str) -> bool:
        """Probe whether the container can accept an exec session right now.

        Runs a cheap CAPTURED `exec <name> true`. Because the output is
        captured, podman's raw "container state improper" race error is
        swallowed instead of leaking to the user's TTY. This is the same
        operation as the interactive bootstrap-attach exec, so a fresh
        success is a tight predictor that the attach will start cleanly.
        Used to gate the TTY-inheriting interactive exec.
        """
        result = subprocess.run(
            [self.cmd, "exec", name, "true"],
            capture_output=True,
        )
        return result.returncode == 0

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

    def inspect_env(self, name: str, key: str) -> str | None:
        """Return the value of env var *key* on container *name*, or None.

        Reads the container's recorded ``.Config.Env`` (the env baked in at
        ``run`` time) and returns the first ``KEY=VALUE`` whose KEY matches.
        Returns None if the container does not exist, the var is unset, or the
        inspect fails — callers fall back to normal resolution in that case.
        """
        result = subprocess.run(
            [self.cmd, "inspect", "--format", "{{json .Config.Env}}", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        try:
            env_list = json.loads(result.stdout.strip() or "null")
        except (ValueError, TypeError):
            return None
        if not isinstance(env_list, list):
            return None
        prefix = f"{key}="
        for item in env_list:
            if isinstance(item, str) and item.startswith(prefix):
                return item[len(prefix):]
        return None

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

    def get_local_digests(self, image: str) -> list[str]:
        """Return all repo digests (``sha256:...``) for a local image.

        Parses ``RepoDigests`` from ``image inspect`` and strips the
        ``repo@`` prefix from each entry. A pulled multi-arch image typically
        records BOTH the per-platform manifest digest and the index digest;
        callers that need to decide freshness want the full set, not just the
        first entry. Returns an empty list on any failure or when the image
        has no repo digests (e.g. locally-built images).
        """
        try:
            result = subprocess.run(
                [self.cmd, "image", "inspect", image, "--format", "json"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return []
            import json
            data = json.loads(result.stdout)
            # podman returns a list, docker returns an object
            if isinstance(data, list):
                data = data[0] if data else {}
            digests = data.get("RepoDigests", []) or []
            out: list[str] = []
            for entry in digests:
                # e.g. "ghcr.io/x/img@sha256:abc..." -> "sha256:abc..."
                out.append(entry.split("@", 1)[1] if "@" in entry else entry)
            return out
        except Exception:
            return []

    def get_local_digest(self, image: str) -> str | None:
        """Return the first repo digest (``sha256:...``) for a local image, or None.

        Kept for callers that need a single stable image key (e.g.
        ``shells.image_store_key``). Delegates to :meth:`get_local_digests`.
        """
        digests = self.get_local_digests(image)
        return digests[0] if digests else None

    def get_local_created(self, image: str) -> str | None:
        """Return the local image build timestamp (``.Created``), or None.

        ``image inspect`` reports ``Created`` as an RFC3339 string. Returns
        None on any failure or when the field is absent/empty.
        """
        data = self.image_inspect(image)
        if not data:
            return None
        created = data.get("Created")
        return created if isinstance(created, str) and created else None

    def get_local_tags(self, image: str) -> list[str]:
        """Return the local image's ``RepoTags`` (``repo:tag`` strings).

        Returns an empty list on any failure or when the image has no repo
        tags (e.g. an image referenced only by digest).
        """
        data = self.image_inspect(image)
        if not data:
            return []
        tags = data.get("RepoTags") or []
        return [t for t in tags if isinstance(t, str)]

    def get_local_label(self, image: str, label: str) -> str | None:
        """Return the value of *label* from the local image's config, or None.

        Reads ``Config.Labels`` (or top-level ``Labels`` as a fallback).
        Returns None on any failure or when the label is absent/empty.
        """
        data = self.image_inspect(image)
        if not data:
            return None
        config = data.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict):
            labels = data.get("Labels")
        if not isinstance(labels, dict):
            return None
        value = labels.get(label)
        return value if isinstance(value, str) and value else None

    def get_local_platform(self, image: str) -> str | None:
        """Return the local image platform as ``os/arch[/variant]``, or None.

        This is the platform we actually run; freshness matches it against the
        per-arch child of a remote image index.
        """
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
            if isinstance(data, list):
                data = data[0] if data else {}
            os_ = data.get("Os")
            arch = data.get("Architecture")
            if not os_ or not arch:
                return None
            platform = f"{os_}/{arch}"
            variant = data.get("Variant")
            if variant:
                platform = f"{platform}/{variant}"
            return platform
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


def _mount_dest_to_host(dest: str, shell_path: Path, project_path: Path) -> Path | None:
    """Map a box-side mount DEST to its host stub path; None if not under home.

    Destinations under ``/home/agent/workspace/`` map relative to
    *project_path*; other destinations under ``/home/agent/`` map relative to
    *shell_path*.  A dest outside the box home returns ``None``.
    """
    workspace = GUEST_HOME + "/workspace/"
    agent_home = GUEST_HOME + "/"
    if dest.startswith(workspace):
        return project_path / dest[len(workspace):]
    if dest.startswith(agent_home):
        return shell_path / dest[len(agent_home):]
    return None


def detect_shadowed_mounts(
    shell_path: Path,
    project_path: Path,
    extra_mounts: list | None,
    enable_vault: bool,
) -> list[str]:
    """Report box-dests whose pre-existing host content a bind will SHADOW.

    A bind mount whose DEST already holds content silently hides that content
    inside the box: the files remain on disk under the OUTER home/workspace
    bind, but the INNER mount shadows them so they are invisible (and untouched)
    in the box.  This detector inspects each candidate dest's mapped host stub
    (the OUTER view) and returns the box-dests that already contain content.

    Candidates: the vault ro/rw dests (when *enable_vault*) plus each
    ``mount.destination`` in *extra_mounts*.  The base roots ``/home/agent`` and
    ``/home/agent/workspace`` are EXCLUDED — their content IS the box, not
    something shadowed (in 1.6.0 the home/workspace base binds flow through
    ``extra_mounts`` too).  Tmpfs masks are not candidates here: masking is
    intentional hiding, and they are not in *extra_mounts*.

    This function is PURE: it performs no filesystem mutation (no mkdir/touch/
    unlink/clear-symlink).  All filesystem probes are best-effort and any
    ``OSError`` skips that dest rather than raising.

    Returns the list of shadowed BOX-DEST strings (e.g. ``/home/agent/vault/rw``).
    """
    candidates: list[str] = []
    if enable_vault:
        candidates.append(f"{GUEST_HOME}/vault/ro")
        candidates.append(f"{GUEST_HOME}/vault/rw")
    for mount in extra_mounts or []:
        candidates.append(mount.destination)

    base_roots = {GUEST_HOME, f"{GUEST_HOME}/workspace"}
    shadowed: list[str] = []
    seen_hosts: set[Path] = set()
    for dest in candidates:
        # Skip the base roots (their content IS the box, not shadowed).
        if dest.rstrip("/") in base_roots:
            continue
        host_path = _mount_dest_to_host(dest, shell_path, project_path)
        if host_path is None:
            continue
        try:
            resolved = host_path.resolve()
        except OSError:
            continue
        if resolved in seen_hosts:
            continue
        seen_hosts.add(resolved)
        try:
            if host_path.is_symlink():
                # A symlink stub is not user content; _precreate clears it.
                continue
            if host_path.is_dir():
                if any(host_path.iterdir()):
                    shadowed.append(dest)
            elif host_path.is_file():
                if host_path.stat().st_size > 0:
                    shadowed.append(dest)
            # else: missing / socket / fifo -> not shadowed.
        except OSError:
            continue  # best-effort; never raise.
    return shadowed


def _precreate_mount_stubs(
    shell_path: Path,
    project_path: Path,
    extra_mounts: list | None,
    enable_vault: bool,
    vault_ro_path: Path,
    vault_rw_path: Path,
    tmpfs_masks: list[str],
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
    def _clear_symlink(p: Path) -> None:
        """Remove *p* if it is a symlink so a bind lands on a clean mountpoint.

        A baked/dirty image may ship ``~/.local/bin/claude`` (or
        ``~/.local/share/claude``) as a symlink into the install-dir subtree.
        If the destination is a symlink, the OCI runtime follows it and the
        bind lands somewhere it gets shadowed ("the bind isn't taking").
        Clearing the symlink first guarantees the bind takes on a real,
        non-symlink mountpoint that we own.
        """
        try:
            if p.is_symlink():
                p.unlink()
                logger.debug("stub cleared symlink: %s", p)
        except OSError as exc:
            logger.debug("stub clear-symlink FAILED: %s (%s)", p, exc)

    def _ensure_dir(p: Path) -> None:
        _clear_symlink(p)
        try:
            p.mkdir(parents=True, exist_ok=True)
            logger.debug("stub mkdir: %s", p)
        except OSError as exc:
            logger.debug("stub mkdir FAILED: %s (%s)", p, exc)

    def _ensure_file(p: Path) -> None:
        _clear_symlink(p)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.touch()
                logger.debug("stub touch: %s", p)
            else:
                logger.debug("stub exists: %s", p)
        except OSError as exc:
            logger.debug("stub touch FAILED: %s (%s)", p, exc)

    # Built-in directory mounts.
    _ensure_dir(shell_path / "workspace")
    if enable_vault:
        # Vault is UNIVERSAL unless disabled: the host source dirs are created
        # if missing by the core-defaults resolver, so the box-side dest stubs
        # are always made whenever vault is enabled.
        _ensure_dir(shell_path / "vault" / "ro")
        _ensure_dir(shell_path / "vault" / "rw")
        # tmpfs mask stubs: one per box-dest in the ``box.masks`` category.
        # Map each box-dest to its host side the same way extra mounts are
        # mapped (under project_path for workspace dests, shell_path for other
        # home dests).  Empty list (the default — no masks) -> no stubs.
        for mask in tmpfs_masks:
            host_path = _mount_dest_to_host(mask, shell_path, project_path)
            if host_path is None:
                logger.debug("mask stub skip (not under home): %s", mask)
                continue
            _ensure_dir(host_path)

    # Extra mounts: pre-create destination stubs.
    if not extra_mounts:
        return
    for mount in extra_mounts:
        dest = mount.destination
        src = mount.source
        host_path = _mount_dest_to_host(dest, shell_path, project_path)
        if host_path is None:
            logger.debug("stub skip (not under home): %s → %s", src, dest)
            continue

        if src.is_dir():
            _ensure_dir(host_path)
        else:
            logger.debug(
                "stub file: src=%s is_file=%s is_dir=%s exists=%s → %s",
                src, src.is_file(), src.is_dir(), src.exists(), host_path,
            )
            _ensure_file(host_path)
