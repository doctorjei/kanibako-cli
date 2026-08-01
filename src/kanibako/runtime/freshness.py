"""Non-blocking image freshness check: warn only when a NEWER image exists.

The check is deliberately conservative: it nags only when it can *prove* the
remote ``:latest`` is newer than what we have locally, and stays silent on any
uncertainty. Two prongs are tried in order:

1. **Semver (preferred).** Resolve a PEP440 version for both the remote
   ``:latest`` and the local image; warn iff ``remote > local``.
2. **Dates (fallback).** When a version can't be resolved on either side,
   compare build ``created`` timestamps; warn iff remote is *strictly* newer.

If neither prong yields a confident answer (can't fetch, unparseable, equal /
zeroed reproducible-build timestamps, a custom image we can't resolve), the
check is silent. It never raises — all exceptions are swallowed so it cannot
block container startup.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version

from kanibako.runtime.container import ContainerRuntime
from kanibako.runtime.registry import (
    get_remote_created,
    get_remote_digests,
    get_remote_tag_digest,
    list_remote_tags,
)

_CACHE_TTL = 86400  # 24 hours
_VERSION_LABEL = "org.opencontainers.image.version"
# Bound the per-tag digest lookups when resolving the remote version: only the
# highest-version tags are probed, since a newer build's :latest can only match
# a recent version tag.
_MAX_VERSION_TAG_PROBES = 12

_BANNER = (
    "Note: A newer version of {image} is available. "
    "Run 'kanibako rig update' to update."
)


def check_image_freshness(runtime: ContainerRuntime, image: str, cache_path: Path) -> None:
    """Print a note to stderr iff a newer *image* is available remotely.

    This function **never** raises — all exceptions are silently swallowed
    so it cannot block container startup.
    """
    try:
        if _is_newer_available(runtime, image, cache_path):
            print(_BANNER.format(image=image), file=sys.stderr)
    except Exception:
        pass


def _is_newer_available(
    runtime: ContainerRuntime, image: str, cache_path: Path,
) -> bool:
    """Return True only when the remote image is *provably* newer than local."""
    platform = runtime.get_local_platform(image)

    remote_digests = _cached_remote_digests(image, platform, cache_path)
    if not remote_digests:
        # Can't see the remote at all -> uncertain -> silent.
        return False

    # --- Prong 1: semver ----------------------------------------------------
    remote_version = _resolve_remote_version(
        image, platform, remote_digests, cache_path,
    )
    local_version = _resolve_local_version(
        runtime, image, remote_digests, remote_version, cache_path,
    )
    if remote_version is not None and local_version is not None:
        return remote_version > local_version

    # If *either* side has a resolvable version we stop here and stay silent.
    # Dates can't tell "newer" apart from "merely rebuilt": a same-version
    # rebuild of remote ``:latest`` (e.g. a base-image refresh) carries a fresh
    # ``created`` stamp, and a locally-built/retagged/pinned image carries its
    # own build time -- both would read as "remote newer" on dates alone. So
    # the date prong is reserved for the case where *neither* side resolves a
    # version (a genuinely custom image); a known local version that we can't
    # prove is behind is treated as up-to-date.
    if remote_version is not None or local_version is not None:
        return False

    # --- Prong 2: dates (only when neither side has a version) --------------
    local_created = _parse_ts(runtime.get_local_created(image))
    remote_created = _parse_ts(_cached_remote_created(image, platform, cache_path))
    if local_created is not None and remote_created is not None:
        return remote_created > local_created

    # Indeterminate on both prongs -> never nag on uncertainty.
    return False


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


def _parse_pep440(tag: str) -> Version | None:
    """Parse *tag* as a PEP440 version, or None if it isn't version-formatted."""
    try:
        return parse_version(tag)
    except (InvalidVersion, TypeError):
        return None


def _version_tags_sorted(image: str, cache_path: Path) -> list[tuple[Version, str]]:
    """Return ``(version, tag)`` for every version-formatted remote tag, desc."""
    tags = _cached_remote_tags(image, cache_path)
    pairs: list[tuple[Version, str]] = []
    for tag in tags:
        ver = _parse_pep440(tag)
        if ver is not None:
            pairs.append((ver, tag))
    pairs.sort(key=lambda p: p[0], reverse=True)
    return pairs


def _resolve_remote_version(
    image: str,
    platform: str | None,
    remote_digests: set[str],
    cache_path: Path,
) -> Version | None:
    """Find the version tag whose digest matches remote ``:latest``.

    Probes the highest-version tags first (bounded) and returns the first whose
    manifest digest is in *remote_digests* (the ``:latest`` digest set).
    """
    for ver, tag in _version_tags_sorted(image, cache_path)[:_MAX_VERSION_TAG_PROBES]:
        digest = _cached_tag_digest(image, tag, cache_path)
        if digest and digest in remote_digests:
            return ver
    return None


def _resolve_local_version(
    runtime: ContainerRuntime,
    image: str,
    remote_digests: set[str],
    remote_version: Version | None,
    cache_path: Path,
) -> Version | None:
    """Resolve the local image's version, in priority order.

    (a) the ``org.opencontainers.image.version`` label;
    (b) a PEP440-parseable RepoTag other than the floating tag in *image*;
    (c) the remote version tag whose digest == our local image digest.
    """
    # (a) label
    label = runtime.get_local_label(image, _VERSION_LABEL)
    ver = _parse_pep440(label) if label else None
    if ver is not None:
        return ver

    # (b) co-tag in RepoTags (skip the floating tag we were invoked with)
    floating = image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else "latest"
    for repo_tag in runtime.get_local_tags(image):
        tag = repo_tag.rsplit(":", 1)[-1] if ":" in repo_tag.rsplit("/", 1)[-1] else ""
        if not tag or tag == floating:
            continue
        ver = _parse_pep440(tag)
        if ver is not None:
            return ver

    # (c) remote version tag whose digest == our local digest
    local_digests = set(runtime.get_local_digests(image))
    if local_digests:
        # If the local image *is* remote :latest, its version is remote_version.
        if remote_version is not None and not local_digests.isdisjoint(remote_digests):
            return remote_version
        for cand_ver, tag in _version_tags_sorted(image, cache_path)[
            :_MAX_VERSION_TAG_PROBES
        ]:
            digest = _cached_tag_digest(image, tag, cache_path)
            if digest and digest in local_digests:
                return cand_ver

    return None


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an RFC3339 timestamp into an aware datetime, or None.

    A zeroed reproducible-build timestamp (the Unix epoch) is treated as
    *absent* so two epoch-stamped images never compare as "newer".
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt == datetime(1970, 1, 1, tzinfo=timezone.utc):
        return None
    return dt


# ---------------------------------------------------------------------------
# Caching (24h, platform-keyed file cache shared with the digest lookup)
# ---------------------------------------------------------------------------


def _cache_key(image: str, platform: str | None) -> str:
    """Cache key combining the image reference and the local platform."""
    return f"{image}\t{platform or ''}"


def _load_cache(cache_path: Path) -> dict:
    cache_file = cache_path / "digest-cache.json"
    if cache_file.is_file():
        try:
            loaded = json.loads(cache_file.read_text())
            if isinstance(loaded, dict):
                return loaded
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _store_cache(cache_path: Path, cache: dict) -> None:
    try:
        cache_path.mkdir(parents=True, exist_ok=True)
        (cache_path / "digest-cache.json").write_text(json.dumps(cache))
    except OSError:
        pass


def _cached_field(
    cache_path: Path,
    key: str,
    field: str,
    fetch,
):
    """Generic 24h-cached single-field lookup keyed by ``key``/``field``.

    Returns the (possibly cached) fetched value. A fresh fetch is performed and
    persisted on a miss; ``None`` results are also cached to avoid repeated
    failing network calls within the TTL.
    """
    now = time.time()
    cache = _load_cache(cache_path)
    entry = cache.get(key)
    if isinstance(entry, dict):
        sub = entry.get(field)
        if isinstance(sub, dict) and now - sub.get("ts", 0) < _CACHE_TTL:
            return sub.get("value")

    value = fetch()
    entry = cache.get(key)
    if not isinstance(entry, dict):
        entry = {}
        cache[key] = entry
    entry[field] = {"value": value, "ts": now}
    _store_cache(cache_path, cache)
    return value


def _cached_remote_digests(
    image: str, platform: str | None, cache_path: Path,
) -> set[str]:
    """Return the acceptable remote digest set, using a 24h file cache.

    The cache stores a list of digests per ``image+platform`` key. Any legacy
    or otherwise-incompatible entry shape is treated as a miss and overwritten.
    """
    now = time.time()
    cache = _load_cache(cache_path)
    key = _cache_key(image, platform)

    entry = cache.get(key)
    if isinstance(entry, dict) and now - entry.get("ts", 0) < _CACHE_TTL:
        digests = entry.get("digests")
        if isinstance(digests, list):
            return set(digests)
        # Incompatible/old shape: fall through to refetch and overwrite.

    digests = get_remote_digests(image, platform)
    if digests:
        # Preserve any sibling cache fields (tags/created/per-tag) on this key.
        if not isinstance(entry, dict):
            entry = {}
            cache[key] = entry
        entry["digests"] = sorted(digests)
        entry["ts"] = now
        _store_cache(cache_path, cache)

    return digests


def _cached_remote_tags(image: str, cache_path: Path) -> list[str]:
    """Remote repository tag list, 24h-cached (tag list is platform-agnostic)."""
    value = _cached_field(
        cache_path,
        _cache_key(image, None),
        "tags",
        lambda: list_remote_tags(image),
    )
    return value if isinstance(value, list) else []


def _cached_tag_digest(image: str, tag: str, cache_path: Path) -> str | None:
    """Per-tag manifest digest, 24h-cached (platform-agnostic tag/index digest)."""
    value = _cached_field(
        cache_path,
        _cache_key(image, None),
        f"tagdigest\t{tag}",
        lambda: get_remote_tag_digest(image, tag),
    )
    return value if isinstance(value, str) else None


def _cached_remote_created(
    image: str, platform: str | None, cache_path: Path,
) -> str | None:
    """Remote ``:latest`` config ``created`` timestamp, 24h-cached per platform."""
    value = _cached_field(
        cache_path,
        _cache_key(image, platform),
        "created",
        lambda: get_remote_created(image, platform),
    )
    return value if isinstance(value, str) else None
