"""Source detection + name derivation for the future ``rig add`` command.

Given a *source* string -- a local file path, a registry reference, or a URL --
these pure helpers decide whether the source describes an OCI **image** (a
prefab to pull / a saved image tar to load) or a buildable **template**
(a Containerfile), and derive a sensible default rig name.

The core helpers do **no network I/O**. A raw ``http(s)://`` URL is undecidable
on its own; the caller is expected to fetch it first via :func:`fetch_to_temp`
(a thin, separately monkeypatchable wrapper) and then classify the downloaded
file with :func:`detect_source_kind`.
"""

from __future__ import annotations

import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# How many leading lines to scan when sniffing a text file for template signals.
_TEMPLATE_SCAN_LINES = 20

# Header conventions mirrored from templates_image.py. We only need to *detect*
# their presence here, so a loose prefix match is enough.
_TEMPLATE_HEADER_RE = re.compile(
    r"^#\s*kanibako-template(?:-check)?:\s*", re.IGNORECASE
)

# A leading ``FROM <image>`` directive marks a Containerfile/Dockerfile.
_FROM_DIRECTIVE_RE = re.compile(r"^\s*FROM\s+\S", re.IGNORECASE)

# Loose OCI registry-reference grammar: ``[registry/]repo[:tag][@digest]``.
# Lowercased before matching. Allows path segments separated by ``/`` and the
# usual ``.``/``-``/``_`` separators within a segment, an optional ``:tag`` and
# an optional ``@sha256:<hex>`` digest.
_REF_RE = re.compile(
    r"^[a-z0-9]+([._-][a-z0-9]+)*"  # first segment (registry host or repo)
    r"(:[0-9]+)?"  # optional :port on the host segment
    r"(/[a-z0-9]+([._-][a-z0-9]+)*)*"  # further /path segments
    r"(:[\w][\w.-]*)?"  # optional :tag
    r"(@sha256:[a-f0-9]+)?$",  # optional @digest
)

# Members whose presence in a tar marks it as an OCI / docker-save image archive.
_IMAGE_TAR_MARKERS = ("manifest.json", "oci-layout")


def fetch_to_temp(url: str) -> Path:
    """Download *url* to a temporary file and return its :class:`Path`.

    Thin wrapper over :func:`urllib.request.urlretrieve` so that callers can
    fetch a remote source before classifying it, and tests can monkeypatch the
    network hop. The temp file is **not** auto-deleted; the caller owns cleanup.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="kanibako-rig-src-")
    # Close our handle; urlretrieve manages the file by name.
    import os

    os.close(fd)
    urllib.request.urlretrieve(url, tmp_name)
    return Path(tmp_name)


def _is_image_tar(path: str) -> bool:
    """Return True if *path* is a tar whose members mark it as an image archive."""
    if not tarfile.is_tarfile(path):
        return False
    try:
        with tarfile.open(path) as tar:
            names = tar.getnames()
    except (tarfile.TarError, OSError):
        return False
    for name in names:
        norm = name.lstrip("./")
        if norm in _IMAGE_TAR_MARKERS:
            return True
        # A top-level ``blobs/`` directory entry (OCI layout layout).
        if norm == "blobs" or norm.startswith("blobs/"):
            return True
    return False


def _has_template_signal(path: Path) -> bool:
    """Return True if *path* reads like a Containerfile/template."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            scanned = 0
            for raw in fh:
                if scanned >= _TEMPLATE_SCAN_LINES:
                    break
                scanned += 1
                line = raw.rstrip("\n")
                if _TEMPLATE_HEADER_RE.match(line):
                    return True
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # First non-empty, non-comment line decides the FROM question.
                return bool(_FROM_DIRECTIVE_RE.match(line))
    except OSError:
        return False
    return False


def detect_source_kind(source: str, *, force: str | None = None) -> str:
    """Classify *source* as ``"image"`` or ``"template"``.

    Resolution order:

    1. *force* in ``{"image", "template"}`` -> returned directly.
    2. **Local file** (``Path(source).is_file()``) -> sniffed: an image-tar
       (manifest.json / oci-layout / blobs/) is ``"image"``; a file with a
       leading ``FROM`` directive or a ``# kanibako-template[-check]:`` header
       is ``"template"``. Neither signal -> :class:`ValueError`.
    3. **Registry reference** (a non-existent path matching the loose ref
       grammar, e.g. ``ghcr.io/corp/base:1.0`` or ``busybox``) -> ``"image"``.

    A raw ``http(s)://`` URL is **undecidable** here -- fetch it first with
    :func:`fetch_to_temp`, then classify the downloaded file. Anything that
    matches none of the above raises :class:`ValueError` with guidance.
    """
    if force is not None:
        if force in ("image", "template"):
            return force
        raise ValueError(
            f"invalid force value '{force}'; expected 'image' or 'template'"
        )

    path = Path(source)
    if path.is_file():
        if _is_image_tar(source):
            return "image"
        if _has_template_signal(path):
            return "template"
        raise ValueError(
            f"cannot classify source '{source}'; pass --as image|template"
        )

    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        raise ValueError(
            f"cannot classify URL '{source}' directly; fetch it first "
            "(fetch_to_temp) then classify the downloaded file."
        )

    if _REF_RE.match(lowered):
        return "image"

    raise ValueError(
        f"cannot classify source '{source}'; expected a local file, an image "
        "reference, or a fetched URL (pass --as image|template to force)."
    )


def _name_from_containerfile_basename(basename: str) -> str | None:
    """Derive a rig name from a ``Containerfile.<rest>`` basename, else None."""
    for prefix in ("Containerfile.", "Dockerfile."):
        if basename.startswith(prefix):
            rest = basename[len(prefix):]
            if not rest:
                return None
            if rest.startswith("template-"):
                rest = rest[len("template-"):]
            return rest or None
    return None


def _name_from_ref(ref: str) -> str | None:
    """Return *ref* minus a leading registry-host segment, if any."""
    if not ref:
        return None
    segments = ref.split("/")
    if len(segments) > 1:
        first = segments[0]
        # A leading segment is a registry host if it carries a ``.`` or a port.
        if "." in first or ":" in first:
            return "/".join(segments[1:]) or None
    return ref


def _name_from_image_tar(path: str) -> str | None:
    """Derive a rig name from an image tar's first RepoTag, else its stem."""
    try:
        with tarfile.open(path) as tar:
            try:
                member = tar.getmember("manifest.json")
            except KeyError:
                member = None
            if member is not None:
                fh = tar.extractfile(member)
                if fh is not None:
                    import json

                    try:
                        manifest = json.loads(fh.read().decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        manifest = None
                    if isinstance(manifest, list) and manifest:
                        tags = manifest[0].get("RepoTags")
                        if isinstance(tags, list) and tags:
                            first = tags[0]
                            if isinstance(first, str) and first:
                                return first
    except (tarfile.TarError, OSError):
        pass
    return Path(path).stem or None


def derive_name(source: str, kind: str) -> str | None:
    """Best-effort default rig name for *source* (of the given *kind*), or None.

    - A Containerfile/Dockerfile path or URL whose basename is
      ``Containerfile.<rest>`` -> ``<rest>`` with an optional leading
      ``template-`` stripped (a bare ``Containerfile``/``Dockerfile`` -> None).
    - An image **reference** -> the ref minus a leading registry-host segment
      (``ghcr.io/corp/base:1.0`` -> ``corp/base:1.0``; ``busybox`` -> ``busybox``).
    - An image **tar** (an existing file) -> its first RepoTag, else the stem.
    - Anything underivable -> None.

    Note: derived image names may legitimately contain ``/`` and ``:`` and are
    *not* run through :func:`kanibako.templates_image.validate_template_name`.
    """
    # A basename-based Containerfile name wins regardless of kind: ``rig add``
    # of a template URL/path should name itself after the file.
    basename = source.rsplit("/", 1)[-1]
    cf_name = _name_from_containerfile_basename(basename)
    if cf_name is not None:
        return cf_name

    if kind == "template":
        return None

    # kind == "image"
    path = Path(source)
    if path.is_file():
        return _name_from_image_tar(source)

    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        # A URL with no Containerfile-style basename is underivable here.
        return None

    return _name_from_ref(source)
