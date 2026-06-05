"""The ``.rig.tgz`` export bundle for *extended* rigs.

Extended rigs have no external source to re-pull or rebuild from, so they
travel as a single self-contained bundle. A bundle is a gzip tar containing:

* ``rig.yaml``      -- the in-image :class:`~kanibako.rig_meta.RigMeta` metadata
* ``image.tar``     -- a ``podman save`` of the rig image
* ``Containerfile`` -- optional, informational build recipe

Packing and unpacking use the stdlib :mod:`tarfile` (gzip) only -- no shelling
out. Extraction is path-traversal-safe.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from kanibako.rig_meta import RigMeta, load_rig_meta

BUNDLE_SUFFIX = ".rig.tgz"

_META_ARCNAME = "rig.yaml"
_IMAGE_ARCNAME = "image.tar"
_CONTAINERFILE_ARCNAME = "Containerfile"


def pack_bundle(
    out: Path,
    rig_yaml: Path,
    image_tar: Path,
    containerfile: Path | None = None,
) -> None:
    """Pack *rig_yaml* + *image_tar* (+ optional *containerfile*) into *out*.

    *out* is created as a gzip tar with the metadata stored as ``rig.yaml``,
    the saved image as ``image.tar`` and, when supplied, the build recipe as
    ``Containerfile``. Raises :class:`FileNotFoundError` if *rig_yaml* or
    *image_tar* is missing.
    """
    if not rig_yaml.is_file():
        raise FileNotFoundError(f"rig metadata not found: {rig_yaml}")
    if not image_tar.is_file():
        raise FileNotFoundError(f"image tarball not found: {image_tar}")

    with tarfile.open(out, "w:gz") as tar:
        tar.add(rig_yaml, arcname=_META_ARCNAME)
        tar.add(image_tar, arcname=_IMAGE_ARCNAME)
        if containerfile is not None:
            tar.add(containerfile, arcname=_CONTAINERFILE_ARCNAME)


def _is_safe_member(name: str) -> bool:
    """Return True if *name* is a safe, in-tree relative arcname.

    Rejects absolute paths and any path that escapes the destination via a
    ``..`` component (guarding against tar path-traversal attacks).
    """
    member = Path(name)
    if member.is_absolute():
        return False
    return ".." not in member.parts


def unpack_bundle(tgz: Path, dest_dir: Path) -> dict[str, Path]:
    """Extract bundle *tgz* into *dest_dir*, returning the member paths.

    Extraction is path-traversal-safe: any member whose name is absolute or
    contains a ``..`` component is rejected (raises :class:`ValueError`). The
    returned dict maps ``"rig_yaml"`` / ``"image_tar"`` to their extracted
    paths, plus ``"containerfile"`` only when one was present. Raises
    :class:`ValueError` if the required ``rig.yaml`` or ``image.tar`` members
    are absent.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tgz, "r:gz") as tar:
        members = tar.getmembers()
        names = {m.name for m in members}

        for member in members:
            if not _is_safe_member(member.name):
                raise ValueError(
                    f"unsafe member name in bundle: {member.name!r}"
                )

        if _META_ARCNAME not in names:
            raise ValueError(f"bundle is missing required '{_META_ARCNAME}'")
        if _IMAGE_ARCNAME not in names:
            raise ValueError(f"bundle is missing required '{_IMAGE_ARCNAME}'")

        for member in members:
            tar.extract(member, path=dest_dir, filter="data")

    result: dict[str, Path] = {
        "rig_yaml": dest_dir / _META_ARCNAME,
        "image_tar": dest_dir / _IMAGE_ARCNAME,
    }
    if _CONTAINERFILE_ARCNAME in names:
        result["containerfile"] = dest_dir / _CONTAINERFILE_ARCNAME
    return result


def read_bundle_meta(tgz: Path) -> RigMeta:
    """Read just the ``rig.yaml`` member of *tgz* and return its :class:`RigMeta`.

    Reads the metadata directly out of the tar without extracting the
    (potentially large) ``image.tar``. Raises :class:`ValueError` if the
    ``rig.yaml`` member is absent.
    """
    with tarfile.open(tgz, "r:gz") as tar:
        try:
            handle = tar.extractfile(_META_ARCNAME)
        except KeyError:
            handle = None
        if handle is None:
            raise ValueError(f"bundle is missing required '{_META_ARCNAME}'")
        with handle:
            text = handle.read().decode("utf-8")

    return load_rig_meta(text)
