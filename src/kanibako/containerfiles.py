"""Containerfile resolution: bundled (package data) with user-override support."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def get_containerfile(suffix: str, data_containers_dir: Path | None = None) -> Path | None:
    """Return the path to a Containerfile for *suffix* (e.g. ``"template-jvm"``).

    Checks user-override directory first, then the bundled package data.
    Returns ``None`` if no matching file exists in either location.
    """
    name = f"Containerfile.{suffix}"

    # 1. User override
    if data_containers_dir is not None:
        override = data_containers_dir / name
        if override.is_file():
            return override

    # 2. Bundled
    bundled = importlib.resources.files("kanibako.containers").joinpath(name)
    try:
        # as_posix on a Traversable; for installed packages this is a real path
        path = Path(str(bundled))
        if path.is_file():
            return path
    except (TypeError, FileNotFoundError):
        pass

    return None


