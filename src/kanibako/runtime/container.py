"""ContainerRuntime: detect podman/docker, pull/build/run images, list images."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from kanibako.errors import ContainerError
from kanibako.log import get_logger
from kanibako.settings_resolve import GUEST_GID, GUEST_HOME, GUEST_UID


logger = get_logger("container")

# User-namespace mapping for every box launch.  Plain ``keep-id`` maps the
# CALLING host user to the same numeric uid inside the container, so a host
# user whose uid != 1000 lands beside — not on — the image's ``agent`` user
# (uid/gid 1000, the GUEST_UID/GUEST_GID image contract), and the ``:U`` bind
# option then recursively chowns the box home AND the user's project tree to
# an unrelated subuid, bricking both.  ``keep-id:uid=…,gid=…`` pins the caller
# onto the agent user regardless of host uid: in-box files are caller-owned,
# ``:U`` chowns resolve to the caller's own uid (a no-op on caller-owned
# trees), and the host-uid==1000 case is unchanged.  Requires podman >= 4.3
# (the ``uid=``/``gid=`` options, 2022-10).  ``keep-id`` is podman-only;
# Docker support is future backlog work with its own userns handling.
KEEP_ID_USERNS = f"--userns=keep-id:uid={GUEST_UID},gid={GUEST_GID}"


# Post-start hook plumbing (see ``ContainerRuntime.run``).  Bounded so a container
# that never comes up cannot leave a thread spinning for the process's lifetime.
_POST_START_TIMEOUT_S = 30.0
_POST_START_POLL_S = 0.25


def _run_post_start(hook: "Callable[[], None]") -> None:
    """Invoke a post-start *hook*, swallowing anything it raises.

    The hook is a repair step, not a precondition: a box whose canon re-protect
    failed is degraded (litter-able) but perfectly usable, and taking the launch
    down over it would trade a cosmetic problem for a total one.
    """
    try:
        hook()
    except Exception as exc:  # noqa: BLE001 - a hook must never break a launch
        logger.debug("post-start hook failed: %s", exc)


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

    def unshare_chown(self, paths: list[Path], uid: int, gid: int) -> bool:
        """``chown uid:gid`` *paths* from within the rootless user namespace.

        The write-side counterpart of :meth:`unshare_rm`, and the mechanism behind
        the canon skeleton's root-owned book roots (J-7): a host user cannot hand a
        file to one of their own subuids directly, but inside ``podman unshare``
        those subuids are ordinary namespace uids.

        ⚑ NO ``-R``.  Callers pass an EXPLICIT, enumerated path list — a recursive
        sweep of ``~/canon`` would take the seeded, agent-owned ``notebook/`` and
        ``workbook/`` books with it.

        Only podman supports ``unshare``; returns False for docker, for an empty
        *paths*, or on any failure.
        """
        return self._unshare_apply(["chown", f"{uid}:{gid}"], paths)

    def unshare_chmod(self, paths: list[Path], mode: str) -> bool:
        """``chmod mode`` *paths* from within the rootless user namespace.

        Runs inside the namespace because the paths are (by then) owned by a subuid
        the host user cannot chmod directly.  Same no-``-R`` rule as
        :meth:`unshare_chown`.
        """
        return self._unshare_apply(["chmod", mode], paths)

    def _unshare_apply(self, argv: list[str], paths: list[Path]) -> bool:
        if not paths:
            return False
        if "podman" not in Path(self.cmd).name:
            return False
        result = subprocess.run(
            [self.cmd, "unshare", *argv, *(str(p) for p in paths)],
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
        post_start: "Callable[[], None] | None" = None,
    ) -> int:
        """Run a container and return the exit code.

        When *detach* is True the container runs in the background (``-d``
        instead of ``-it``, no ``--rm``).  Returns 0 on success.

        ⚑ *post_start* runs after podman has set the box's mounts up.  On the
        DETACHED path that is once, inline, the moment ``podman run -d`` returns.  On
        the FOREGROUND path it runs when the session ends (and, opportunistically,
        from a watcher as soon as the container is seen running) — see below.
        It is the seam for work that must happen AFTER podman has set the box's
        mounts up — specifically the canon re-protect, because the home bind's
        ``:U`` option makes podman RECURSIVELY RE-CHOWN the home bind SOURCE to the
        container user's mapping at container creation, which resets the box-create
        canon skeleton from container-root back to the agent (bifrost, 2026-07-31:
        host ``165536 165536 555`` post-create → ``1000 1000 555`` post-start).

        The hook is wired HERE, rather than as a separate step after each
        ``runtime.run(...)``, so that the ORDERING (and the detach/foreground split
        below) exists in one place instead of being re-derived at every call site.

        ⚑ IT IS NOT "COVERED BY CONSTRUCTION" — the parameter is OPT-IN, so a call
        site that does not pass *post_start* gets nothing.  What IS true by
        construction is narrower and worth stating exactly: ``run()`` is the only
        container-CREATION seam (stopped containers are ``rm``'d and recreated, never
        ``podman start``ed), so there is no OTHER place a ``:U`` chown can happen.
        Every call site that binds a box home must pass the hook itself; that
        obligation is enforced by a test
        (``test_container.TestPostStartCallSites``), not by this signature.
        """
        masks = tmpfs_masks or []
        # Pre-create mount destination stubs so crun doesn't need to mkdir
        # inside bind-mounted overlay filesystems (fails in LXC).
        _precreate_mount_stubs(
            shell_path, project_path, extra_mounts,
            enable_vault, vault_ro_path, vault_rw_path, masks,
        )

        if detach:
            run_flags = ["-dt", KEEP_ID_USERNS]
        else:
            tty_flag = "-it" if sys.stdin.isatty() else "-i"
            run_flags = [tty_flag, "--rm", KEEP_ID_USERNS]
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
                # The container is up the instant ``podman run -d`` returns, so the
                # hook runs INLINE here — no polling, no thread.
                if post_start is not None:
                    _run_post_start(post_start)
            return result.returncode

        # Interactive foreground path: inherit the terminal so the agent /
        # shell (and tmux attach) get the real stdio/tty.
        #
        # ⚑ This call BLOCKS for the whole session, so there is no "after start"
        # moment in this thread — the hook has to run from a watcher that waits for
        # the container to come up alongside it.  Without this the ephemeral/shell
        # modes would run their entire session with an agent-owned canon (the
        # detached path would be protected and the foreground path silently not).
        watcher = None
        if post_start is not None and name:
            watcher = self._watch_for_start(name, post_start)
        try:
            fg_result = subprocess.run(cmd)
        finally:
            if watcher is not None:
                watcher.set()
            # ⚑ THE GUARANTEE LIVES HERE, NOT IN THE WATCHER.  A container that
            # lives less than one poll interval is never observed running (measured:
            # up at 20ms, gone at 50ms — the first probe fires before the subprocess
            # even starts and the second lands after this cancel), so the watcher
            # alone would leave short-lived ephemeral boxes NEVER repaired, silently
            # and invisibly to any e2e using a long-running stub.  Re-asserting once
            # the container is gone costs one idempotent pass and makes the on-disk
            # state ALWAYS end protected.  The watcher is now only an optimisation:
            # it shortens the window DURING a long session.
            if post_start is not None:
                _run_post_start(post_start)
        return fg_result.returncode

    def _watch_for_start(
        self, name: str, post_start: "Callable[[], None]",
    ) -> "threading.Event":
        """Fire *post_start* once *name* is running; return a cancel Event.

        A short-lived DAEMON thread: it cannot keep the process alive, it polls a
        bounded number of times, and every exception is swallowed and debug-logged.
        A launch must never fail because a post-start hook did.
        """
        cancelled = threading.Event()

        def _wait() -> None:
            deadline = time.monotonic() + _POST_START_TIMEOUT_S
            while not cancelled.is_set() and time.monotonic() < deadline:
                try:
                    if self.is_running(name):
                        _run_post_start(post_start)
                        return
                except Exception as exc:  # noqa: BLE001 - never break a launch
                    logger.debug("post-start watcher probe failed: %s", exc)
                cancelled.wait(_POST_START_POLL_S)
            logger.debug("post-start watcher gave up waiting for %s", name)

        threading.Thread(target=_wait, daemon=True, name=f"kanibako-poststart-{name}").start()
        return cancelled

    def exec(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        attach: bool = False,
    ) -> int:
        """Run a command inside a running container. Interactive (inherits stdio).

        Returns the exit code of the exec'd process.

        *attach* marks this exec as a BOOTSTRAP ATTACH (a ``tmux attach`` handoff)
        rather than a command whose output is the user's payload.  It only changes
        the NON-tty case: an attach has no session to render without a terminal, and
        the caller surfaces the agent's real logs/exit via ``podman logs``, so the
        runtime's own raw race error — e.g. ``container state improper`` when a
        supervised box tore down between the readiness probe and this exec — is
        CAPTURED instead of leaking to the caller's stderr as if it were agent
        output.  A NON-attach exec (the default; the one-off ``kanibako shell <box>
        -- <cmd>`` path) always inherits stdio so its output reaches the user, tty or
        not.
        """
        # Allocate a pty only when stdin is a real terminal. In scripted /
        # subprocess contexts (CI, e2e tests), -t causes interactive commands
        # like ``tmux attach`` to render but never return.
        interactive = sys.stdin.isatty()
        tty_flag = "-it" if interactive else "-i"
        cmd: list[str] = [self.cmd, "exec", tty_flag]
        if env:
            for k, v in sorted(env.items()):
                cmd += ["-e", f"{k}={v}"]
        cmd.append(name)
        cmd.extend(command)

        logger.debug("Container exec: %s", cmd)
        if not attach:
            # A NON-attach exec whose output IS the user's payload (the one-off
            # ``kanibako shell <box> -- <cmd>`` path): inherit all stdio so the
            # output reaches the user, tty or not.
            return subprocess.run(cmd).returncode
        # Bootstrap ATTACH (a ``tmux attach`` handoff).  In BOTH cases we capture
        # stderr so the runtime's raw race error (``container state improper`` / ``can
        # only create exec sessions on running containers`` when a supervised box tore
        # down between the readiness probe and this attach) does NOT leak to the
        # caller's stderr as if it were agent output — the caller re-checks liveness
        # and surfaces the agent's real logs/exit, so that noise is debug-only.
        if interactive:
            # Real terminal: keep stdin/stdout on the TTY so the interactive tmux
            # session renders and the user drives it (tmux draws to stdout, errors to
            # stderr).  stdout is a terminal, so it drains itself — no deadlock.
            result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        else:
            # Scripted / non-tty: there is no session to render, so capture BOTH
            # streams.  ⚑ Capturing stdout is REQUIRED, not just tidy: inheriting it
            # to the caller's (undrained) pipe lets a live ``tmux attach`` fill the
            # pipe buffer and DEADLOCK until the caller's timeout.  Draining it here
            # both prevents that wedge and keeps the raw error off the caller's stdio.
            result = subprocess.run(cmd, capture_output=True, text=True)
        err = (result.stderr or "").strip()
        if err:
            logger.debug("attach exec rc=%s stderr=%s", result.returncode, err)
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

    def container_image(self, name: str) -> str | None:
        """Return the image reference container *name* was created from, or None.

        Reads the running container's ``.ImageName`` (the fully-qualified image
        reference recorded at ``run`` time).  Returns None if the container does
        not exist, the field is empty, or the inspect fails — callers fall back
        to the configured ``box_image``.

        Note: ``.ImageName`` is podman-specific; ``docker inspect`` has no such
        field, so on Docker this errors → returns None → caller degrades to the
        ``box_image`` config fallback (Docker is backlog, not yet supported).
        """
        result = subprocess.run(
            [self.cmd, "inspect", "--format", "{{.ImageName}}", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

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


def remove_box_tree(target: Path) -> bool:
    """Remove *target*, tolerating files a rootless container created.

    THE box-tree deleter.  A box's home can contain files owned by mapped subuids —
    files a ``--userns=keep-id`` container wrote as root, and (since J-7) the
    root-owned canon skeleton kanibako itself creates at box create — which the host
    user cannot unlink.  A plain :func:`shutil.rmtree` then fails with EACCES, so
    this falls back to ``podman unshare rm -rf``, which deletes from within the user
    namespace.  Returns True if *target* is gone afterwards, False otherwise (callers
    warn rather than crash).

    ⚑ EVERY verb that deletes a box home or box metadata tree must come through
    here — ``rm --purge``, ``extract``, ``move``/``convert``, ``duplicate``'s
    ``--force``/rollback arms and ``purge``.  A bare ``rmtree`` on any of those paths
    is a bug: it fails on the canon skeleton and leaves a half-deleted box behind.

    THREE ESCALATING ATTEMPTS, because there are three distinct failure shapes:

    1. plain ``rmtree`` — ordinary content;
    2. ``rmtree`` after re-opening the directory modes we OWN.  ⚑ ``rmtree`` raises
       ``PermissionError`` on a tree containing 555 directories EVEN WHEN THE CALLER
       OWNS THEM (unlinking a child needs write on its parent), and an owned-but-555
       canon skeleton is genuinely reachable: ``shutil.copytree`` reproduces the
       skeleton's modes without its ownership, so every box-home copy passes through
       that state, as does a create whose ``chown`` failed after the ``chmod``. This
       step needs no container runtime at all, which matters because the machines
       that most often lack podman are exactly the ones running these verbs;
    3. ``podman unshare rm -rf`` — the SUBUID case, where the files are not ours and
       no amount of chmod helps.

    ⚑ DEGRADED END-STATE, stated because it is not obvious: if step 2 re-opened some
    modes and steps 2 AND 3 then both failed, the tree is left BEHIND with those dir
    modes re-opened — i.e. LESS protected than before the attempt.  That is the right
    trade (the caller is trying to delete the tree, and a half-deletable box is worse
    than a briefly-writable one) but a caller that reports the False return must not
    imply the tree is untouched.
    """
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        pass

    # (2) Re-open the modes on directories WE own, then retry.  Bounded to *target*'s
    # own subtree, best-effort per entry, and it never touches a dir owned by someone
    # else (the chmod simply fails and is skipped — that is case 3's job).
    reopened = False
    for root, dirs, _files in os.walk(target, topdown=True):
        for d in (root, *(os.path.join(root, x) for x in dirs)):
            try:
                # ⚑ NEVER chmod THROUGH a symlink.  ``os.chmod`` follows links, so a
                # symlinked dir inside the box home would have its TARGET re-opened —
                # possibly somewhere outside the tree we are deleting.  On Linux this
                # is safe today only by accident (a symlink's own lstat mode is 0777,
                # so the ``not S_IWUSR`` test skips it); make the safety DELIBERATE
                # rather than a property of one platform's mode bits.  ``os.walk``
                # does not follow directory symlinks either, so skipping here loses
                # nothing: rmtree unlinks the link itself, which needs only the
                # parent's write bit.
                if os.path.islink(d):
                    continue
                mode = stat.S_IMODE(os.lstat(d).st_mode)
                if not mode & stat.S_IWUSR:
                    os.chmod(d, mode | stat.S_IRWXU)
                    reopened = True
            except OSError:
                continue
    if reopened:
        try:
            shutil.rmtree(target)
            return True
        except OSError:
            pass

    try:
        if ContainerRuntime().unshare_rm(target):
            return True
    except ContainerError:
        pass
    return not target.exists()


# The MANAGED CANON region inside a box (J-7).  Every bind dest at or under it lands
# on a mountpoint the box-create skeleton already materialised, root-owned and 555.
_CANON_GUEST_PREFIX = f"{GUEST_HOME}/canon"


def _is_managed_canon_dest(dest: str) -> bool:
    """True for a bind dest the CANON SKELETON owns, which must not be stubbed.

    ⚑ PATH-SHAPED, not key-shaped, and deliberately so: EVERY bind under ``~/canon``
    is by construction one of the canon book binds, whose mountpoint
    :func:`kanibako.core_defaults.materialize_canon_skeleton` pre-created at box
    create.  One uniform rule beats six per-key special cases.  The seeded
    ``canon/notebook`` + ``canon/workbook`` are never binds, so they never reach here.
    """
    return dest == _CANON_GUEST_PREFIX or dest.startswith(f"{_CANON_GUEST_PREFIX}/")


def _guest_dest_to_host(
    dest: str,
    shell_path: Path,
    project_path: Path,
    *,
    map_home_root: bool = False,
) -> Path | None:
    """Map a box-side guest DEST to its host stub path; None if not under home.

    The SINGLE translator shared by the mount-stub precreate/shadow scans (here)
    and the seed/synced COPY appliers (:mod:`kanibako.commands.start`).
    Destinations under ``/home/agent/workspace/`` map relative to *project_path*
    (the workspace bind); other destinations under ``/home/agent/`` map relative
    to *shell_path* (the box HOME bind).  A dest outside the box home returns
    ``None``.

    *map_home_root* controls the box-home ROOT itself (``/home/agent``, with any
    trailing slash):

    * ``False`` (default — the mount-stub callers): the bare home root returns
      ``None``.  The base home bind is not a stub to pre-create, and the shadow
      scan skips the base roots explicitly.
    * ``True`` (the seed/synced COPY callers): the bare home root maps to
      *shell_path* so a ``~``-targeted copy (the ``seeded.template`` trio) stages
      straight into the box HOME.

    Both COPY callers gain the ``/workspace`` split for free by routing here: a
    ``~/workspace/...`` copy dest now lands under *project_path* (the workspace
    bind), not the shadowed ``shell_path/workspace`` stub — closing the former
    inline translators' latent drift bug (audit P3).
    """
    if map_home_root and dest.rstrip("/") == GUEST_HOME:
        return shell_path
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
        host_path = _guest_dest_to_host(dest, shell_path, project_path)
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

    def _loosen_parents(stub: Path, root: Path) -> None:
        """Add SEARCH bits to *stub*'s parent dirs up to (not incl.) *root*.

        WHY: crun's openat2 destination resolution must TRAVERSE every parent of
        a bind dest.  A pre-existing box home commonly ships XDG dirs like
        ``~/.config`` at 0700 (gh/podman/XDG tools create them private); a file
        bind placed under such a dir (the agent kickoff SEED at
        ``~/.config/kanibako/kickoff.md``, new in rc12) then dies at launch with
        ``crun: creating ... openat2 home/agent/.config: Permission denied``
        (exit 126).  Fresh boxes never hit it because our own mkdir makes
        ``.config`` traversable; pre-existing homes do.

        WHY only ``0o011`` (g+x, o+x — the SEARCH bits, no read): +x alone makes
        a directory TRAVERSABLE without exposing a listing, so directory contents
        stay unlistable/private.  It is the MINIMUM that makes the path resolve.

        Containment (load-bearing — this mutates user-visible modes in the box
        home, so it is deliberately narrow):
          * *root* is *shell_path* ONLY; callers never pass a project_path
            (workspace) dest — that is the user's real tree and loosening its
            modes is a policy call we are not making this increment.
          * Never chmod AT or ABOVE *root*: the walk stops when it reaches root,
            so root itself and its ancestors are untouched.
          * A SYMLINKED parent stops the walk — we must not follow a symlink OUT
            of the box home and chmod a target elsewhere (belt-and-suspenders:
            ``resolve().relative_to`` below also catches an escaping ancestor).
          * Only parents of ACTUAL bind dests are visited, so a private 0700 dir
            with no bind under it (e.g. ``~/.ssh``) is never touched.

        Best-effort like the sibling stub helpers: any ``OSError`` is
        debug-logged and swallowed — a pre-create hiccup must never abort a
        launch (crun may still succeed, or the real error surfaces there).
        """
        try:
            root_resolved = root.resolve()
        except OSError as exc:
            logger.debug("loosen skip (root resolve FAILED): %s (%s)", root, exc)
            return
        # Start at the stub's PARENT: the stub's own mode is owned by the bind.
        # Walk LEXICAL parents upward and test each against *root*.
        current = stub.parent
        while True:
            # Stop AT root (and never above it).  ``resolve()`` collapses any
            # symlinked ancestor, so an escape resolves OUTSIDE root -> ValueError
            # -> we stop rather than chmod something beyond the box home.
            try:
                rel = current.resolve().relative_to(root_resolved)
            except (OSError, ValueError):
                break
            if rel == Path("."):
                # Reached root itself — off-limits, and nothing above it either.
                break
            try:
                # A symlinked parent would let the chmod escape the box home even
                # if its target resolves back inside — stop the walk here.
                if current.is_symlink():
                    logger.debug("loosen stop at symlink parent: %s", current)
                    break
                perm = stat.S_IMODE(current.stat().st_mode)
                if perm & 0o011 != 0o011:
                    new_perm = perm | 0o011
                    current.chmod(new_perm)
                    logger.info(
                        "loosened box-home dir for bind traversal: %s %04o -> %04o",
                        current, perm, new_perm,
                    )
            except OSError as exc:
                # Tolerant like the sibling stub helpers: an unreadable/vanished
                # parent must never abort the launch.  Stop the walk — we can no
                # longer safely reason about ancestors we cannot probe.
                logger.debug("loosen probe/chmod FAILED: %s (%s)", current, exc)
                break
            current = current.parent

    def _ensure_dir(p: Path, traverse_root: Path | None = None) -> None:
        _clear_symlink(p)
        try:
            p.mkdir(parents=True, exist_ok=True)
            logger.debug("stub mkdir: %s", p)
        except OSError as exc:
            logger.debug("stub mkdir FAILED: %s (%s)", p, exc)
        # Loosen only home-side (shell_path) parents; project_path dests pass None.
        if traverse_root is not None:
            _loosen_parents(p, traverse_root)

    def _ensure_file(p: Path, traverse_root: Path | None = None) -> None:
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
        # Loosen only home-side (shell_path) parents; project_path dests pass None.
        if traverse_root is not None:
            _loosen_parents(p, traverse_root)

    # A dest maps to the shell_path (box HOME) side unless it lives under
    # ``/home/agent/workspace/`` (the project tree).  Only home-side parents are
    # loosened for traversal; workspace parents are the user's real tree (see
    # ``_loosen_parents``), so they pass ``traverse_root=None``.
    workspace_prefix = GUEST_HOME + "/workspace/"

    def _home_root(dest: str) -> Path | None:
        return None if dest.startswith(workspace_prefix) else shell_path

    # Built-in directory mounts.  These are all shell_path-side; the workspace
    # stub's only parent IS shell_path, so its loosen walk is a no-op.
    _ensure_dir(shell_path / "workspace", traverse_root=shell_path)
    if enable_vault:
        # Vault is UNIVERSAL unless disabled: the host source dirs are created
        # if missing by the core-defaults resolver, so the box-side dest stubs
        # are always made whenever vault is enabled.
        _ensure_dir(shell_path / "vault" / "ro", traverse_root=shell_path)
        _ensure_dir(shell_path / "vault" / "rw", traverse_root=shell_path)
        # tmpfs mask stubs: one per box-dest in the ``box.masks`` category.
        # Map each box-dest to its host side the same way extra mounts are
        # mapped (under project_path for workspace dests, shell_path for other
        # home dests).  Empty list (the default — no masks) -> no stubs.
        for mask in tmpfs_masks:
            host_path = _guest_dest_to_host(mask, shell_path, project_path)
            if host_path is None:
                logger.debug("mask stub skip (not under home): %s", mask)
                continue
            _ensure_dir(host_path, traverse_root=_home_root(mask))

    # Extra mounts: pre-create destination stubs.
    if not extra_mounts:
        return
    for mount in extra_mounts:
        dest = mount.destination
        src = mount.source
        host_path = _guest_dest_to_host(dest, shell_path, project_path)
        # ⚑ THE CANON SKELETON OWNS ITS OWN MOUNTPOINTS (J-7) — BUT ONLY WHERE IT
        # EXISTS.  On a box created by R1b the mountpoint is already there,
        # root-owned and unwritable, so stubbing it is pointless and wrong-headed.
        #
        # ⚑ THE SKIP IS EXISTENCE-AWARE, NOT PATH-AWARE, and that distinction is
        # load-bearing.  Boxes with NO skeleton exist and will keep arriving: every
        # R1-era and pre-canon box, plus any box whose create hit the degraded path.
        # An unconditional skip leaves their five-or-six canon binds with no
        # mountpoints at all, and in LXC crun cannot mkdir inside a bind-mounted
        # overlay — that is a launch failure (exit 126), not a degradation. Falling
        # through to the tolerant stub helpers instead pre-creates the mountpoints
        # for exactly those boxes, which also makes this a free self-healing
        # migration: an old box gains its canon mountpoints on its next launch.
        #
        # Where the skeleton DOES exist the fall-through would be a no-op anyway
        # (``_ensure_dir``/``_ensure_file`` are create-if-absent, and
        # ``_loosen_parents`` finds 555 already carrying the 0o011 search bits), so
        # the skip buys clarity rather than behaviour — it says out loud that these
        # mountpoints are not the launch path's to manage.
        if host_path is not None and _is_managed_canon_dest(dest):
            if host_path.exists():
                logger.debug("stub skip (canon skeleton owns it): %s → %s", src, dest)
                continue
            logger.debug(
                "canon mountpoint absent (pre-R1b box?) — stubbing: %s → %s", src, dest,
            )
        if host_path is None:
            logger.debug("stub skip (not under home): %s → %s", src, dest)
            continue

        if src.is_dir():
            _ensure_dir(host_path, traverse_root=_home_root(dest))
        else:
            logger.debug(
                "stub file: src=%s is_file=%s is_dir=%s exists=%s → %s",
                src, src.is_file(), src.is_dir(), src.exists(), host_path,
            )
            _ensure_file(host_path, traverse_root=_home_root(dest))
