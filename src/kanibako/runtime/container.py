"""ContainerRuntime: detect podman/docker, pull/build/run images, list images.

Terminology.  *Box* — a running container; *rig* — the image it runs from.  *Stub* — a
host-side mountpoint pre-created so the OCI runtime never has to mkdir a bind dest.
*Mask* — an empty read-only tmpfs emitted over a box-dest; a VOID, with nothing inside
it.  *Shadowing* — pre-existing host content at a bind DEST, hidden (not deleted) by the
bind that lands on it.  *Managed canon* — the root-owned 555 skeleton box-create
materialises under ``~/canon``, whose mountpoints the launch path does not manage.

⚑ The ``⚑`` comments below record PLATFORM behaviour, not design, and most of it cannot
be tested from a box without podman.  Eight such claims are UNVERIFIED here and listed
under "UNVERIFIED on this box" in ``llm-docs/kanibako/runtime/container.py.md``; do not
delete one for lack of a covering test.
"""

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
from kanibako.settings.core_defaults import CANON_SEED_DENY_PREFIXES
from kanibako.settings.settings_resolve import GUEST_GID, GUEST_HOME, GUEST_UID


logger = get_logger("container")

# ⚑ NOT plain ``keep-id``: the ``uid=``/``gid=`` pin is what stops ``:U`` recursively
# chowning the box home and project tree to a stray subuid.  Needs podman >= 4.3.
KEEP_ID_USERNS = f"--userns=keep-id:uid={GUEST_UID},gid={GUEST_GID}"


# Post-start hook plumbing: bounded so a box that never comes up cannot spin a thread forever.
_POST_START_TIMEOUT_S = 30.0
_POST_START_POLL_S = 0.25


def _run_post_start(hook: "Callable[[], None]") -> None:
    """Invoke a post-start *hook*, swallowing anything it raises."""
    try:
        hook()
    except Exception as exc:  # noqa: BLE001 - a hook must never break a launch
        logger.debug("post-start hook failed: %s", exc)


# The smallest rule a string must satisfy to be an image reference AT ALL: a registry
# host, a repository path, an optional ``:tag``, an optional ``@sha256:…``.  Deliberately
# NOT the distribution/reference grammar — it is here to reject what cannot be a
# reference, never to certify what is.
_IMAGE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*\Z")


def image_ref_or_none(raw: str) -> str | None:
    """Return ``inspect`` output *raw* as an image reference, or None if it cannot be one.

    The ONE guard for every ``inspect --format`` read of an image reference — the local
    leg (:meth:`ContainerRuntime.container_image`) and the remote one
    (``vscode.vscode_remote.RemoteEngine.container_image``) both route through it, so the
    two cannot disagree about what a reference is.

    ⚑ Why an exit-0 read needs a guard at all: Go's ``text/template`` errors on a missing
    STRUCT field but prints the literal ``<no value>`` — at exit 0 — for a missing MAP
    key, so an engine rendering inspect templates against a decoded map hands back a
    string that is not a reference.  The engine is not ours to constrain: ``self.cmd`` is
    whatever ``KANIBAKO_DOCKER_CMD`` or ``$PATH`` supplies.  Unguarded, such a value keys
    a VS Code attached-container config — and a settings image tier — off ``"<no value>"``.

    Deliberately NOT checked: that the reference names an image that exists, or that it is
    fully qualified.  A caller wanting either must ask the engine.
    """
    ref = raw.strip()
    if _IMAGE_REF_RE.match(ref):
        return ref
    if ref:
        logger.debug("container inspect returned a non-reference image value: %r", ref)
    return None


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
        """Remove *path* from within the rootless user namespace; False for docker/failure."""
        if "podman" not in Path(self.cmd).name:
            return False
        result = subprocess.run(
            [self.cmd, "unshare", "rm", "-rf", str(path)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def unshare_chown(self, paths: list[Path], uid: int, gid: int) -> bool:
        """``chown uid:gid`` *paths* from within the rootless user namespace."""
        # ⚑ NO ``-R``: callers pass an EXPLICIT list — a recursive sweep of ``~/canon``
        # would take the seeded, agent-owned ``notebook/``/``workbook/`` books with it.
        return self._unshare_apply(["chown", f"{uid}:{gid}"], paths)

    def unshare_chmod(self, paths: list[Path], mode: str) -> bool:
        """``chmod mode`` *paths* from within the rootless user namespace."""
        # ⚑ Same no-``-R`` rule as :meth:`unshare_chown`.
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
        """Copy *src* into a container at *dest* (``<container>:<path>``); True on success."""
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
        """Load an image from the tar *archive*; returns its ref, or None if load failed."""
        result = subprocess.run(
            [self.cmd, "load", "-i", str(archive)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        # ⚑ THREE observed output shapes: ``Loaded image:``, ``Loaded image(s):``, ``Loaded image ID:``.
        for line in result.stdout.splitlines():
            m = re.search(r"Loaded image(?:\(s\)| ID)?:\s*(\S.*)$", line)
            if m:
                return m.group(1).strip()
        return ""

    def diff(self, image: str) -> list[str]:
        """Return the changed paths for *image* as verbatim lines; empty list on failure."""
        result = subprocess.run(
            [self.cmd, "diff", image],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def ensure_image(self, image: str, containers_dir: Path | None = None) -> None:
        """Make sure *image* is available locally: inspect, then pull.  Base images are PULL-ONLY."""
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
        """Run a container and return the exit code; *detach* backgrounds it (``-dt``, no ``--rm``)."""
        masks = tmpfs_masks or []
        # Stubs first: crun cannot mkdir a bind dest inside a bind-mounted overlay (LXC).
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
            # ⚑ NO hardwired binds: home/workspace/vault arrive via *extra_mounts* (single route).
            "-w", f"{GUEST_HOME}/workspace",
        ]
        # ⚑ ``notmpcopyup`` IS LOAD-BEARING: the ``tmpcopyup`` default copies the dest's
        # content UP, downgrading the path to read-only instead of emptying it.  A mask is
        # a VOID.  Dropping it regresses SILENTLY — every test stays green.
        # ⚑ OUTSIDE any vault arm, and it must STAY outside: a mask is a user key of its own.
        for mask in masks:
            cmd += ["--mount", f"type=tmpfs,dst={mask},ro,notmpcopyup"]
        # ⚑ MASKS BEFORE BINDS, and the order is load-bearing: it is why a bind whose dest
        # sits under a mask still takes at runtime.  Binds go via ``-v`` — the ONLY
        # ``--mount`` in this argv is the tmpfs above.
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
            # Capture the container id podman prints; the caller reattaches by NAME.
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
                # The box is up the instant ``run -d`` returns: hook runs INLINE, no watcher.
                if post_start is not None:
                    _run_post_start(post_start)
            return result.returncode

        # Foreground: inherit the terminal, and BLOCK for the whole session — so there is no
        # "after start" moment in this thread and the hook needs a watcher alongside it.
        watcher = None
        if post_start is not None and name:
            watcher = self._watch_for_start(name, post_start)
        try:
            fg_result = subprocess.run(cmd)
        finally:
            if watcher is not None:
                watcher.set()
            # ⚑ THE GUARANTEE LIVES HERE, NOT IN THE WATCHER: a box shorter-lived than one
            # poll interval is never observed running, so this UNCONDITIONAL re-assert is
            # what makes the on-disk state always end protected.  Never gate it on *watcher*.
            if post_start is not None:
                _run_post_start(post_start)
        return fg_result.returncode

    def _watch_for_start(
        self, name: str, post_start: "Callable[[], None]",
    ) -> "threading.Event":
        """Fire *post_start* once *name* is running; return a cancel Event."""
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
        """Run a command in a running container; *attach* marks a ``tmux attach`` handoff."""
        # ⚑ pty ONLY on a real terminal: under CI/subprocess, ``-t`` makes ``tmux attach``
        # render but never return.
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
            # Output IS the user's payload: inherit all stdio, tty or not.
            return subprocess.run(cmd).returncode
        # ⚑ Attach captures stderr in BOTH arms so podman's teardown race error does not
        # leak to the caller as if it were agent output.
        if interactive:
            # Real terminal: stdout stays on the TTY, so it drains itself — no deadlock.
            result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        else:
            # ⚑ Capturing stdout here is REQUIRED, not tidy: inherited to an undrained
            # caller pipe, a live ``tmux attach`` fills the buffer and DEADLOCKS.
            result = subprocess.run(cmd, capture_output=True, text=True)
        err = (result.stderr or "").strip()
        if err:
            logger.debug("attach exec rc=%s stderr=%s", result.returncode, err)
        return result.returncode

    def exec_ready(self, name: str) -> bool:
        """Probe whether the container can accept an exec session right now."""
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
        """Return the value of env var *key* recorded on container *name*, or None."""
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
        """Return the image reference container *name* was created from, or None."""
        # ⚑ ``.ImageName`` is a PODMAN field, and docker REFUSES it rather than printing
        # Go's ``<no value>``: docker/cli renders the template against the typed struct
        # first, then retries against the raw JSON with ``missingkey=error``
        # (``cli/command/inspect/inspector.go``), so an unknown TOP-LEVEL field exits 1 —
        # pinned by moby's own ``TestInspectTemplateError`` (``{{.ThisDoesNotExist}}`` →
        # "template parsing error").  READ FROM SOURCE, never measured: no docker here.
        # ⚑ It was NOT always so — docker built with Go 1.4 (pre-1.12) printed
        # ``<no value>`` at exit 0 (moby#15566) — which is why the rc check below is
        # backed by :func:`image_ref_or_none` instead of trusted on its own.
        result = subprocess.run(
            [self.cmd, "inspect", "--format", "{{.ImageName}}", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return image_ref_or_none(result.stdout)

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
        """Return ALL repo digests (``sha256:...``) for a local image; empty list on failure."""
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
        """Return the FIRST repo digest (``sha256:...``) for a local image, or None."""
        digests = self.get_local_digests(image)
        return digests[0] if digests else None

    def get_local_created(self, image: str) -> str | None:
        """Return the local image build timestamp (``.Created``, RFC3339), or None."""
        data = self.image_inspect(image)
        if not data:
            return None
        created = data.get("Created")
        return created if isinstance(created, str) and created else None

    def get_local_tags(self, image: str) -> list[str]:
        """Return the local image's ``RepoTags`` (``repo:tag`` strings); empty on failure."""
        data = self.image_inspect(image)
        if not data:
            return []
        tags = data.get("RepoTags") or []
        return [t for t in tags if isinstance(t, str)]

    def get_local_label(self, image: str, label: str) -> str | None:
        """Return the value of *label* from the local image's config, or None."""
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
        """Return the local image platform as ``os/arch[/variant]``, or None."""
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
    """Remove *target*, tolerating files a rootless container created — THE box-tree deleter.

    ⚑ EVERY verb deleting a box home or metadata tree must come through here; a bare
    ``rmtree`` on those paths is a bug.  THREE ESCALATING ATTEMPTS — see the llm-doc.
    """
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        pass

    # (2) Re-open the modes on directories WE own, then retry.  A dir owned by someone
    # else simply fails the chmod and is skipped — that is attempt 3's job.
    reopened = False
    for root, dirs, _files in os.walk(target, topdown=True):
        for d in (root, *(os.path.join(root, x) for x in dirs)):
            try:
                # ⚑ NEVER chmod THROUGH a symlink: ``os.chmod`` follows links and would
                # re-open a TARGET outside the tree being deleted.  Deliberate, not
                # incidental — do not drop this as redundant with the mode test below.
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


# The MANAGED CANON region inside a box (J-7): box-create materialises these mountpoints.
_CANON_GUEST_PREFIX = f"{GUEST_HOME}/canon"


def _is_managed_canon_dest(dest: str) -> bool:
    """True for a bind dest the CANON SKELETON owns, which must not be stubbed."""
    # ⚑ PATH-shaped, not key-shaped, deliberately: one uniform rule, not six per-key cases.
    return dest == _CANON_GUEST_PREFIX or dest.startswith(f"{_CANON_GUEST_PREFIX}/")


# The MANAGED CANON REGION, as guest-absolute prefixes — same set spec §2c forbids a template
# SEED from targeting (``CANON_SEED_DENY_PREFIXES``), widened from ``~``-relative to guest-
# absolute here.  ⚑ NARROWER than ``_CANON_GUEST_PREFIX`` above: it excludes
# ``canon/{notebook,workbook}``, which stay genuinely seedable, so a bind there can still
# shadow real user content and must keep reporting.
_CANON_SEED_DENY_GUEST_PREFIXES = tuple(
    f"{GUEST_HOME}/{rel}" for rel in CANON_SEED_DENY_PREFIXES
)


def _is_seed_denied_canon_dest(dest: str) -> bool:
    """True for a bind dest under the seed-denied managed canon region (spec §2c).

    Box-create's skeleton owns these dests and no seed may ever land under them, so a bind
    here can never shadow user content — it is not a candidate for the shadow report at all.
    """
    rstripped = dest.rstrip("/")
    return any(
        rstripped == prefix or rstripped.startswith(f"{prefix}/")
        for prefix in _CANON_SEED_DENY_GUEST_PREFIXES
    )


def _guest_dest_to_host(
    dest: str,
    shell_path: Path,
    project_path: Path,
    *,
    map_home_root: bool = False,
) -> Path | None:
    """Map a box-side guest DEST to its host stub path; None if not under home.

    ⚑ THE SINGLE translator — stub/shadow scans here, seed/synced COPY appliers there.
    *map_home_root* maps the bare home root: ``None`` (stubs) vs *shell_path* (copies).
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

    ⚑ PURE: probes only, no mkdir/touch/unlink/clear-symlink, and every ``OSError``
    skips that dest rather than raising.  Do not add mutation here.
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
        # Skip the seed-denied managed canon region (spec §2c): box-create's own skeleton
        # owns these dests, so a bind here is guaranteed benign — reporting it would fire on
        # EVERY box and could never indicate a real mistake (finding #7).
        if _is_seed_denied_canon_dest(dest):
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
    """Pre-create mount destination stubs to avoid crun permission errors."""
    def _clear_symlink(p: Path) -> None:
        """Remove *p* if it is a symlink so a bind lands on a clean mountpoint."""
        try:
            if p.is_symlink():
                p.unlink()
                logger.debug("stub cleared symlink: %s", p)
        except OSError as exc:
            logger.debug("stub clear-symlink FAILED: %s (%s)", p, exc)

    def _loosen_parents(stub: Path, root: Path) -> None:
        """Add SEARCH bits to *stub*'s parent dirs up to (not incl.) *root*.

        ⚑ This MUTATES user-visible modes in the box home, so its containment is
        load-bearing and deliberately narrow — see the llm-doc before widening it.
        """
        try:
            root_resolved = root.resolve()
        except OSError as exc:
            logger.debug("loosen skip (root resolve FAILED): %s (%s)", root, exc)
            return
        # Start at the stub's PARENT: the stub's own mode is owned by the bind.
        current = stub.parent
        while True:
            # ⚑ Stop AT root, never above: an escaping ancestor resolves OUTSIDE root and
            # raises ValueError, so we stop rather than chmod beyond the box home.
            try:
                rel = current.resolve().relative_to(root_resolved)
            except (OSError, ValueError):
                break
            if rel == Path("."):
                # Reached root itself — off-limits, and nothing above it either.
                break
            try:
                # ⚑ A symlinked parent could escape the box home even if it resolves
                # back inside — stop the walk rather than chmod through it.
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
                # Stop the walk: we cannot reason about ancestors we cannot probe.
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

    # ⚑ Only HOME-side parents are loosened; workspace parents are the user's real tree
    # and pass ``traverse_root=None``.
    workspace_prefix = GUEST_HOME + "/workspace/"

    def _home_root(dest: str) -> Path | None:
        return None if dest.startswith(workspace_prefix) else shell_path

    # Built-in directory mounts — all shell_path-side, so their loosen walks are no-ops.
    _ensure_dir(shell_path / "workspace", traverse_root=shell_path)
    if enable_vault:
        # Vault is UNIVERSAL unless disabled, so its dest stubs are always made.
        _ensure_dir(shell_path / "vault" / "ro", traverse_root=shell_path)
        _ensure_dir(shell_path / "vault" / "rw", traverse_root=shell_path)
    # ⚑ Mask stubs sit OUTSIDE the vault arm on purpose and must STAY outside: ``run``
    # emits a declared mask vault-or-not, and without its stub the mount fails in LXC.
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
        # ⚑ THE SKIP IS EXISTENCE-AWARE, NOT PATH-AWARE, and that is load-bearing: a
        # skeleton-less box (pre-canon, or a degraded create) must FALL THROUGH and get
        # its canon mountpoints stubbed, or crun fails the launch in LXC (exit 126).
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
