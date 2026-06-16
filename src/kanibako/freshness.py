"""Non-blocking image freshness check: warn when a newer image is available."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from kanibako.container import ContainerRuntime
from kanibako.registry import get_remote_digests

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
    local_digests = runtime.get_local_digests(image)
    if not local_digests:
        return

    platform = runtime.get_local_platform(image)

    remote_acceptable = _cached_remote_digests(image, platform, cache_path)
    if not remote_acceptable:
        return

    # Warn only when the local image shares no digest with the set that means
    # "current" for our platform (per-arch child and/or index digest).
    if set(local_digests).isdisjoint(remote_acceptable):
        print(
            f"Note: A newer version of {image} is available. "
            f"Run 'kanibako rig rebuild' to update.",
            file=sys.stderr,
        )


def _cache_key(image: str, platform: str | None) -> str:
    """Cache key combining the image reference and the local platform."""
    return f"{image}\t{platform or ''}"


def _cached_remote_digests(
    image: str, platform: str | None, cache_path: Path,
) -> set[str]:
    """Return the acceptable remote digest set, using a 24h file cache.

    The cache stores a list of digests per ``image+platform`` key. Any legacy
    or otherwise-incompatible entry shape is treated as a miss and overwritten.
    """
    cache_file = cache_path / "digest-cache.json"
    now = time.time()

    cache: dict = {}
    if cache_file.is_file():
        try:
            loaded = json.loads(cache_file.read_text())
            if isinstance(loaded, dict):
                cache = loaded
        except (json.JSONDecodeError, OSError):
            cache = {}

    key = _cache_key(image, platform)
    entry = cache.get(key)
    if isinstance(entry, dict) and now - entry.get("ts", 0) < _CACHE_TTL:
        digests = entry.get("digests")
        if isinstance(digests, list):
            return set(digests)
        # Incompatible/old shape: fall through to refetch and overwrite.

    digests = get_remote_digests(image, platform)
    if digests:
        cache[key] = {"digests": sorted(digests), "ts": now}
        try:
            cache_path.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache))
        except OSError:
            pass

    return digests
