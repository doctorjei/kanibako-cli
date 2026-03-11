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
  with ``--userns=keep-id`` (which kanibako already uses).
- The shared store is read-only; the child can pull new images on top but
  cannot modify or delete shared layers.
- Only overlay storage driver is supported (the default for rootless podman).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kanibako.log import get_logger
from kanibako.targets.base import Mount

logger = get_logger("image_sharing")

# Well-known mount point inside child containers.
SHARED_STORE_CONTAINER_PATH = "/var/lib/shared-images"

# Container-side storage.conf path (rootless podman).
_STORAGE_CONF_CONTAINER_PATH = "/home/agent/.config/containers/storage.conf"


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
    graph_root = detect_graph_root(runtime_cmd)
    if graph_root is None:
        logger.info("Image sharing: could not detect host graph root, skipping")
        return []

    logger.info("Image sharing: host graph root at %s", graph_root)

    # Generate the storage.conf snippet
    storage_conf_content = generate_storage_conf(SHARED_STORE_CONTAINER_PATH)
    staging_dir.mkdir(parents=True, exist_ok=True)
    storage_conf_path = staging_dir / "storage.conf"
    storage_conf_path.write_text(storage_conf_content)

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
