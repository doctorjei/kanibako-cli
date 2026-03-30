"""tweakcc binary cache with flock-based reference counting.

Patched Claude Code binaries are cached to avoid redundant tweakcc runs.
The cache directory can be on tmpfs (for RAM speed and auto-cleanup on
reboot) or regular disk — kanibako doesn't care.

Usage::

    cache = TweakccCache(Path("~/.cache/kanibako/tweakcc"))
    key = cache.cache_key(binary_hash, cfg_hash)
    entry = cache.get(key)
    if entry is None:
        def patch_fn(staging_dir, binary_path):
            subprocess.run(["podman", "run", "--rm", ...], check=True)
        entry = cache.put(key, source_binary, patch_fn)
    # ... use entry.path as the patched binary ...
    cache.release(entry)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kanibako.log import get_logger

logger = get_logger("tweakcc_cache")


class TweakccCacheError(Exception):
    """Error during tweakcc cache operations."""


@dataclass
class CacheEntry:
    """A locked reference to a cached patched binary."""

    path: Path
    fd: int


def config_hash(config: dict) -> str:
    """SHA-256 hex digest of a config dict (deterministic JSON)."""
    serialized = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


class TweakccCache:
    """Cache for tweakcc-patched binaries with flock reference counting.

    Each cached binary is identified by a key derived from the source
    binary's cli.js hash and the tweakcc config hash.  Active users hold
    shared flocks; cleanup happens when no shared locks remain.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def ensure_dir(self) -> None:
        """Create the cache directory if it doesn't exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, cli_js_hash: str, cfg_hash: str) -> str:
        """Derive a short cache key from binary and config hashes."""
        combined = f"{cli_js_hash}:{cfg_hash}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def _entry_path(self, key: str) -> Path:
        return self.cache_dir / key

    def get(self, key: str) -> CacheEntry | None:
        """Look up a cached binary and acquire a shared lock.

        Returns *None* on cache miss (file missing or unlockable).
        """
        path = self._entry_path(key)
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except FileNotFoundError:
            return None

        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            logger.debug("Cache hit: %s", key)
            return CacheEntry(path=path, fd=fd)
        except OSError:
            os.close(fd)
            return None

    def put(
        self,
        key: str,
        source_binary: Path,
        patch_fn: Callable[[Path, Path], None],
    ) -> CacheEntry:
        """Copy *source_binary* to a staging dir, patch it, cache the result.

        *patch_fn(staging_dir, binary_path)* is called with the staging
        directory and the path to the copied binary within it.  The callable
        must modify the binary in-place (it may create temp files in
        *staging_dir*).

        Raises :class:`TweakccCacheError` if *patch_fn* raises.
        """
        self.ensure_dir()
        entry_path = self._entry_path(key)
        staging_dir = self.cache_dir / f".staging-{key}-{os.getpid()}"

        try:
            staging_dir.mkdir(parents=True)
            staging_binary = staging_dir / source_binary.name
            shutil.copy2(str(source_binary), str(staging_binary))
            staging_binary.chmod(0o755)

            # Delegate patching to the caller-supplied function
            logger.debug("Running patch_fn in staging dir: %s", staging_dir)
            patch_fn(staging_dir, staging_binary)

            # Move patched binary to cache entry
            os.rename(str(staging_binary), str(entry_path))
            logger.debug("Cached patched binary: %s", key)

            # Acquire shared lock on the cached entry
            fd = os.open(str(entry_path), os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_SH)
            return CacheEntry(path=entry_path, fd=fd)

        except TweakccCacheError:
            raise
        except Exception as exc:
            raise TweakccCacheError(f"Cache put failed: {exc}") from exc
        finally:
            # Clean up staging directory
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

    def release(self, entry: CacheEntry) -> bool:
        """Release a cache entry.  Unlinks the file if no other users remain.

        Returns *True* if the file was unlinked, *False* otherwise.
        The fd is always closed.
        """
        os.close(entry.fd)

        # Best-effort cleanup: try exclusive lock on the file
        try:
            fd = os.open(str(entry.path), os.O_RDONLY)
        except FileNotFoundError:
            return True  # already gone

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # We're the only one — safe to unlink
            try:
                os.unlink(str(entry.path))
                logger.debug("Cleaned up cache entry: %s", entry.path.name)
                return True
            except FileNotFoundError:
                return True
            finally:
                os.close(fd)
        except OSError:
            # Another process holds a lock — leave it
            os.close(fd)
            return False
