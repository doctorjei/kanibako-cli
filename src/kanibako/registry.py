"""OCI Distribution API client for querying remote image digests (stdlib only)."""

from __future__ import annotations

import json
import urllib.request
import urllib.error

_TIMEOUT = 5

# All four manifest media types we may encounter, so the registry returns the
# canonical (per-tag) digest regardless of whether the tag is a plain manifest
# or an index/manifest-list.
_ACCEPT = ", ".join(
    (
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)

_INDEX_MEDIA_TYPES = frozenset(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)


def get_remote_digest(image: str) -> str | None:
    """Return the remote manifest (tag/index) digest for *image*, or None.

    Retained for callers that only need the single tag digest. Freshness
    should use :func:`get_remote_digests`, which also resolves the matching
    per-arch child of an image index.
    """
    try:
        registry, repo, tag = _parse_image_ref(image)
        token = _get_anonymous_token(registry, repo)
        return _fetch_manifest_digest(registry, repo, tag, token)
    except Exception:
        return None


def get_remote_digests(image: str, platform: str | None) -> set[str]:
    """Return the set of remote digests that mean "current" for *platform*.

    The set always includes the tag/index digest itself. When the tag is an
    image index (manifest list), the body is fetched and the child manifest
    whose ``platform.os``/``architecture`` (and ``variant`` if present) matches
    *platform* is added; ``unknown/unknown`` attestation children are skipped.
    For a plain manifest the tag digest already is the per-arch identity, so
    nothing else is added. Returns an empty set on any failure.
    """
    try:
        registry, repo, tag = _parse_image_ref(image)
        token = _get_anonymous_token(registry, repo)

        result: set[str] = set()
        tag_digest, media_type = _fetch_manifest_meta(registry, repo, tag, token)
        if tag_digest:
            result.add(tag_digest)

        if media_type in _INDEX_MEDIA_TYPES and platform:
            child = _resolve_index_child(registry, repo, tag, token, platform)
            if child:
                result.add(child)

        return result
    except Exception:
        return set()


def _parse_image_ref(image: str) -> tuple[str, str, str]:
    """Split ``registry/owner/name:tag`` into ``(registry, owner/name, tag)``.

    Raises ``ValueError`` if the format is unrecognised.
    """
    # Strip tag
    if ":" in image.rsplit("/", 1)[-1]:
        ref, tag = image.rsplit(":", 1)
    else:
        ref, tag = image, "latest"

    parts = ref.split("/")
    if len(parts) < 3:
        raise ValueError(f"Cannot parse image reference: {image}")
    registry = parts[0]
    repo = "/".join(parts[1:])
    return registry, repo, tag


def _get_anonymous_token(registry: str, repo: str) -> str | None:
    """Obtain a bearer token for anonymous pulls (GHCR only for now)."""
    if registry != "ghcr.io":
        return None

    url = f"https://ghcr.io/token?scope=repository:{repo}:pull"
    req = urllib.request.Request(url, headers={"User-Agent": "kanibako"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read())
    return data.get("token")


def _manifest_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": "kanibako", "Accept": _ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_manifest_digest(
    registry: str, repo: str, tag: str, token: str | None,
) -> str | None:
    """HEAD the manifest to read ``Docker-Content-Digest``."""
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(
        url, method="HEAD", headers=_manifest_headers(token),
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        digest = resp.headers.get("Docker-Content-Digest")
    return digest


def _fetch_manifest_meta(
    registry: str, repo: str, tag: str, token: str | None,
) -> tuple[str | None, str | None]:
    """HEAD the manifest, returning ``(digest, content_type)``."""
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(
        url, method="HEAD", headers=_manifest_headers(token),
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        digest = resp.headers.get("Docker-Content-Digest")
        content_type = resp.headers.get("Content-Type")
    return digest, content_type


def _resolve_index_child(
    registry: str, repo: str, tag: str, token: str | None, platform: str,
) -> str | None:
    """GET the index body and return the child digest matching *platform*.

    *platform* is ``os/arch`` or ``os/arch/variant``. ``unknown/unknown``
    attestation children are skipped. Returns None when no child matches.
    """
    want = platform.split("/")
    want_os = want[0] if len(want) > 0 else None
    want_arch = want[1] if len(want) > 1 else None
    want_variant = want[2] if len(want) > 2 else None

    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(url, method="GET", headers=_manifest_headers(token))
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        body = json.loads(resp.read())

    for child in body.get("manifests", []) or []:
        plat = child.get("platform") or {}
        c_os = plat.get("os")
        c_arch = plat.get("architecture")
        # Skip buildx attestation/provenance children.
        if c_os == "unknown" or c_arch == "unknown":
            continue
        if c_os != want_os or c_arch != want_arch:
            continue
        # Only require variant match when both sides declare one.
        c_variant = plat.get("variant")
        if want_variant and c_variant and c_variant != want_variant:
            continue
        digest = child.get("digest")
        if digest:
            return digest
    return None
