"""Nested container image sharing: mount host image storage read-only into child containers.

When kanibako runs inside a container (LXC/VM), nested podman pulls images
separately, duplicating hundreds of MB.  This module provides opt-in sharing
of the host's image storage via podman's ``additionalImageStores`` mechanism.

The host's overlay storage is bind-mounted **read-only** into the child
container at a well-known path, and a ``storage.conf`` snippet is generated
so the child's podman can find the shared layers.

**Known limitations**:

- UID mapping: if host and child rootless podman use different subuid ranges,
  layer files may be inaccessible.  Works best when the child container runs
  in keep-id mode (kanibako uses ``--userns=keep-id:uid=…,gid=…``, mapping the
  caller onto the in-box agent user, so caller-owned layer files stay readable).
- The shared store is read-only; the child can pull new images on top but
  cannot modify or delete shared layers.
- Only overlay storage driver is supported (the default for rootless podman).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kanibako.log import get_logger
from kanibako.settings_resolve import GUEST_HOME
from kanibako.targets.base import Mount

logger = get_logger("image_sharing")

# Well-known mount point inside child containers.
SHARED_STORE_CONTAINER_PATH = "/var/lib/shared-images"

# Container-side storage.conf path (rootless podman).
_STORAGE_CONF_CONTAINER_PATH = f"{GUEST_HOME}/.config/containers/storage.conf"


def detect_graph_root(runtime_cmd: str) -> Path | None:
    """Detect the host podman/docker graph root (image storage directory).

    Returns the ``GraphRoot`` path from ``podman info`` / ``docker info``,
    or *None* if detection fails.
    """
    try:
        result = subprocess.run(
            [runtime_cmd, "info", "--format", "{{.Store.GraphRoot}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug(
                "Failed to detect graph root: %s", result.stderr.strip(),
            )
            return None
        graph_root = result.stdout.strip()
        if not graph_root:
            return None
        path = Path(graph_root)
        if not path.is_dir():
            logger.debug("Graph root does not exist: %s", path)
            return None
        return path
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Graph root detection failed: %s", exc)
        return None


def is_rootless_podman(runtime_cmd: str) -> bool | None:
    """Return whether *runtime_cmd* is a rootless podman runtime.

    Reads ``Host.Security.Rootless`` from ``podman info``.  Returns:

    * ``True``  — rootless podman (the case that cannot overlay on virtiofs).
    * ``False`` — rootful podman / docker / a runtime that reports not-rootless.
    * ``None``  — could not determine (command failed, unparseable output, or
      the runtime isn't podman).  Callers MUST treat ``None`` as "unknown" and
      never fire a diagnostic on it.

    A rootful shim (``KANIBAKO_DOCKER_CMD`` set, or docker) reports rootless
    ``false`` here, so the virtiofs preflight stays silent for it.
    """
    if "podman" not in Path(runtime_cmd).name:
        # docker (or anything non-podman) is not the rootless-overlay case.
        return False
    try:
        result = subprocess.run(
            [runtime_cmd, "info", "--format", "{{.Host.Security.Rootless}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug("Failed to query rootless: %s", result.stderr.strip())
            return None
        value = result.stdout.strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Rootless detection failed: %s", exc)
        return None


def path_fstype(path: Path) -> str | None:
    """Return the filesystem type backing *path*, or *None* if undeterminable.

    Walks ``/proc/self/mountinfo`` (pure-Python, side-effect-free, no
    subprocess) and returns the fstype of the longest mount point that is a
    prefix of *path*'s real location.  Returns *None* on any uncertainty
    (no procfs, parse failure, no matching mount) so callers can skip silently.
    """
    try:
        target = os.path.realpath(path)
    except OSError:
        return None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    best_len = -1
    best_fstype: str | None = None
    for line in lines:
        # mountinfo: <id> <pid> <maj:min> <root> <mountpoint> <opts> ... - <fstype> <src> <superopts>
        # The fstype follows the " - " separator; fields before it are
        # space-separated and the mount point is field index 4.
        try:
            pre, post = line.split(" - ", 1)
        except ValueError:
            continue
        pre_fields = pre.split()
        post_fields = post.split()
        if len(pre_fields) < 5 or not post_fields:
            continue
        mountpoint = pre_fields[4]
        fstype = post_fields[0]
        # Match mountpoint as a path prefix of the target (longest wins).
        if target == mountpoint or target.startswith(
            mountpoint.rstrip("/") + "/"
        ) or mountpoint == "/":
            if len(mountpoint) > best_len:
                best_len = len(mountpoint)
                best_fstype = fstype
    return best_fstype


# Actionable diagnostic for the unsupported rootless-podman-on-virtiofs case.
# {graph_root} is filled with the detected host graph root path.
VIRTIOFS_GRAPHROOT_MESSAGE = (
    "Error: podman's image storage is on a virtiofs filesystem.\n"
    "  graph root: {graph_root}\n"
    "  Rootless podman cannot overlay-mount or pivot_root on virtiofs, so the\n"
    "  box cannot launch (otherwise you'd hit a cryptic 'pivot_root: permission\n"
    "  denied' / overlay error from the runtime).\n"
    "  This can happen if you use kento to compose VM images.\n"
    "\n"
    "  Fix it one of these ways:\n"
    "    - Back podman's graph root with a real filesystem (a real-disk or\n"
    "      loopback-backed ext4) instead of the virtiofs share.\n"
    "    - Use a rootful runtime by pointing KANIBAKO_DOCKER_CMD at a rootful\n"
    "      podman/docker shim (rootful overlay works on virtiofs).\n"
    "\n"
    "  Note: rootless podman on a virtiofs graph root is an unsupported\n"
    "  configuration."
)


def virtiofs_graphroot_message(runtime_cmd: str) -> str | None:
    """Return the friendly virtiofs diagnostic, or *None* if it doesn't apply.

    The message is returned ONLY when ALL of these hold:

    * ``KANIBAKO_DOCKER_CMD`` is NOT set (a rootful shim handles virtiofs), and
    * the runtime is **rootless podman** (``is_rootless_podman`` is ``True``), and
    * the host graph root's filesystem is **virtiofs**.

    On any uncertainty — rootless state unknown, graph root undetectable, or
    fstype undeterminable — returns *None* so a normal launch is never broken.
    The check is cheap (one ``podman info`` for rootless + a procfs read) and
    side-effect-free.
    """
    # A rootful shim handles virtiofs fine; never fire when one is configured.
    if os.environ.get("KANIBAKO_DOCKER_CMD"):
        return None
    if is_rootless_podman(runtime_cmd) is not True:
        return None
    graph_root = detect_graph_root(runtime_cmd)
    if graph_root is None:
        return None
    if path_fstype(graph_root) != "virtiofs":
        return None
    return VIRTIOFS_GRAPHROOT_MESSAGE.format(graph_root=graph_root)


def generate_storage_conf(shared_store_path: str) -> str:
    """Generate a ``storage.conf`` snippet for podman's additionalImageStores.

    The generated config tells the child's podman to use the overlay driver
    and look for additional (read-only) image layers at *shared_store_path*.

    Parameters
    ----------
    shared_store_path:
        The container-side path where the host's graph root is mounted
        (typically ``/var/lib/shared-images``).
    """
    return (
        "[storage]\n"
        '  driver = "overlay"\n'
        "  [storage.options]\n"
        f'    additionalimagestores = ["{shared_store_path}"]\n'
    )


def prepare_image_sharing_sources(
    runtime_cmd: str,
    staging_dir: Path,
) -> tuple[Path, Path] | None:
    """Probe the host graph root + GENERATE the storage.conf, returning their paths.

    The SOURCE-resolver half of image sharing (spec D-M8 = generated+bound), split
    out so the box-launch seam can inject these runtime-probed/generated host paths
    into a keyed ``box.bindings.ro.images_*`` binding (routed through the category
    resolver) rather than hardwiring two :class:`Mount`s.

    Detects the host ``GraphRoot`` and writes the generated ``storage.conf`` into
    *staging_dir*; returns ``(graph_root, storage_conf_path)`` on success, or
    *None* if the host graph root can't be detected (caller skips sharing).
    """
    graph_root = detect_graph_root(runtime_cmd)
    if graph_root is None:
        logger.info("Image sharing: could not detect host graph root, skipping")
        return None

    logger.info("Image sharing: host graph root at %s", graph_root)

    storage_conf_content = generate_storage_conf(SHARED_STORE_CONTAINER_PATH)
    staging_dir.mkdir(parents=True, exist_ok=True)
    storage_conf_path = staging_dir / "storage.conf"
    storage_conf_path.write_text(storage_conf_content)

    return graph_root, storage_conf_path


def build_image_sharing_mounts(
    runtime_cmd: str,
    staging_dir: Path,
) -> list[Mount]:
    """Build the bind-mounts needed for image sharing.

    Returns a list of mounts (may be empty if detection fails or the
    storage path doesn't exist).  On success returns two mounts:

    1. Host graph root -> ``/var/lib/shared-images`` (read-only)
    2. Generated ``storage.conf`` -> child's config dir (read-only)

    Parameters
    ----------
    runtime_cmd:
        Path to the container runtime binary (``podman`` or ``docker``).
    staging_dir:
        A host-side directory where the generated ``storage.conf`` will be
        written.  Should be under the project's metadata or cache path.
    """
    sources = prepare_image_sharing_sources(runtime_cmd, staging_dir)
    if sources is None:
        return []
    graph_root, storage_conf_path = sources

    return [
        Mount(
            source=graph_root,
            destination=SHARED_STORE_CONTAINER_PATH,
            options="ro",
        ),
        Mount(
            source=storage_conf_path,
            destination=_STORAGE_CONF_CONTAINER_PATH,
            options="ro",
        ),
    ]
