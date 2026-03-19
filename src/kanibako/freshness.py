"""Non-blocking image freshness check: warn when a newer image is available."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from kanibako.container import ContainerRuntime
from kanibako.registry import get_remote_digest

_CACHE_TTL = 86400  # 24 hours


def check_image_freshness(runtime: ContainerRuntime, image: str, cache_path: Path) -> None:
    """Compare local and remote digests; print a note to stderr if stale.

    This function **never** raises — all exceptions are silently swallowed
    so it cannot block container startup.
    """
    try:
        _check(runtime, image, cache_path)
    except Exception:
        pass


def _check(runtime: ContainerRuntime, image: str, cache_path: Path) -> None:
    local_digest = runtime.get_local_digest(image)
    if local_digest is None:
        return

    remote_digest = _cached_remote_digest(image, cache_path)
    if remote_digest is None:
        return

    if local_digest != remote_digest:
        print(
            f"Note: A newer version of {image} is available. "
            f"Run 'kanibako rig rebuild' to update.",
            file=sys.stderr,
        )


def _cached_remote_digest(image: str, cache_path: Path) -> str | None:
    """Return the remote digest, using a 24h file cache."""
    cache_file = cache_path / "digest-cache.json"
    now = time.time()

    cache: dict = {}
    if cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}

    entry = cache.get(image)
    if entry and now - entry.get("ts", 0) < _CACHE_TTL:
        return entry.get("digest")

    digest = get_remote_digest(image)
    if digest is not None:
        cache[image] = {"digest": digest, "ts": now}
        cache_path.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))

    return digest
