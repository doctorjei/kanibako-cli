"""Template image management: create, list, delete user templates."""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path
from typing import NamedTuple, TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.container import ContainerRuntime

_TEMPLATE_PREFIX = "kanibako-template-"
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Bundled template Containerfiles follow this naming convention. Only files
# named exactly ``Containerfile.template-<name>`` (with a valid <name>) are
# treated as shipped templates -- this excludes ``Containerfile.kanibako``
# (the buildable base) and any non-matching files for free.
_TEMPLATE_FILE_PREFIX = "Containerfile.template-"

# Optional description header inside a template Containerfile, e.g.
#   # kanibako-template: Java, Kotlin, Maven (JVM toolchain)
_DESC_HEADER_RE = re.compile(r"^#\s*kanibako-template:\s*(.+?)\s*$")

# Read at most this many lines looking for the description header.
_DESC_HEADER_SCAN_LINES = 10


def validate_template_name(name: str) -> None:
    """Raise *ValueError* if *name* contains invalid characters.

    Template names must start with a lowercase letter or digit and contain
    only lowercase letters, digits, hyphens, and underscores.
    """
    if not _VALID_NAME_RE.match(name):
        raise ValueError(
            f"Invalid template name '{name}': must contain only lowercase "
            "letters, digits, hyphens, and underscores, and must start with "
            "a letter or digit."
        )


def template_image_name(name: str) -> str:
    """Return the OCI image name for a template.

    Raises *ValueError* if *name* is invalid.
    """
    validate_template_name(name)
    return f"{_TEMPLATE_PREFIX}{name}"


class BundledTemplate(NamedTuple):
    """A template Containerfile available to kanibako.

    *source* is ``"bundled"`` for templates shipped with kanibako and
    ``"user"`` for templates dropped into the user-override directory.
    """

    name: str
    description: str
    source: str = "bundled"


def _bundled_containers_dir() -> Path | None:
    """Return the path to kanibako's shipped ``containers/`` directory.

    Returns ``None`` if the package data cannot be resolved as a real
    filesystem path (e.g. running from a zip import).
    """
    try:
        traversable = importlib.resources.files("kanibako.containers")
        path = Path(str(traversable))
    except (TypeError, FileNotFoundError):
        return None
    return path if path.is_dir() else None


def _read_template_description(containerfile: Path, name: str) -> str:
    """Return the ``# kanibako-template:`` header text, or a fallback label."""
    try:
        with containerfile.open(encoding="utf-8") as fh:
            for _, line in zip(range(_DESC_HEADER_SCAN_LINES), fh):
                match = _DESC_HEADER_RE.match(line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return f"{name} template"


def _scan_template_dir(
    containers_dir: Path | None, source: str
) -> list[BundledTemplate]:
    """Scan *containers_dir* for ``Containerfile.template-<name>`` files.

    Returns a list of :class:`BundledTemplate` tagged with *source*. Invalid
    names and non-matching files are skipped. Order is the raw iteration order
    (the caller is responsible for any sorting/merging).
    """
    if containers_dir is None or not containers_dir.is_dir():
        return []

    templates: list[BundledTemplate] = []
    for entry in containers_dir.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.startswith(_TEMPLATE_FILE_PREFIX):
            continue
        name = entry.name[len(_TEMPLATE_FILE_PREFIX):]
        if not _VALID_NAME_RE.match(name):
            continue
        description = _read_template_description(entry, name)
        templates.append(
            BundledTemplate(name=name, description=description, source=source)
        )
    return templates


def list_bundled_templates(
    containers_dir: Path | None = None,
    *,
    override_dir: Path | None = None,
) -> list[BundledTemplate]:
    """Discover template Containerfiles, merging bundled and user overrides.

    Scans *containers_dir* (defaulting to kanibako's bundled ``containers/``
    directory) non-recursively for files named exactly
    ``Containerfile.template-<name>`` where ``<name>`` is a valid template
    name (source ``"bundled"``). The ``archive/`` subdirectory and
    non-matching files (such as ``Containerfile.kanibako``) are excluded.

    If *override_dir* is a directory, it is scanned the same way for
    user-dropped templates (source ``"user"``). A user template with the same
    ``<name>`` as a bundled one *overrides* it -- mirroring
    :func:`kanibako.containerfiles.get_containerfile`'s override-first
    precedence -- so the result carries the user file's description and
    ``source="user"``.

    Each template's description is taken from a ``# kanibako-template: <desc>``
    header comment near the top of the file, falling back to ``"<name>
    template"`` when absent. Results are sorted by name.
    """
    if containers_dir is None:
        containers_dir = _bundled_containers_dir()

    merged: dict[str, BundledTemplate] = {}
    for tmpl in _scan_template_dir(containers_dir, "bundled"):
        merged[tmpl.name] = tmpl
    for tmpl in _scan_template_dir(override_dir, "user"):
        merged[tmpl.name] = tmpl

    return sorted(merged.values(), key=lambda t: t.name)


def list_templates(runtime: ContainerRuntime) -> list[tuple[str, str, str]]:
    """Return (short_name, full_image, size) for all local template images."""
    images = runtime.list_local_images()
    result = []
    for repo, size in images:
        # Strip tag if present for matching
        bare = repo.split(":")[0] if ":" in repo else repo
        if bare.startswith(_TEMPLATE_PREFIX):
            short = bare[len(_TEMPLATE_PREFIX):]
            result.append((short, bare, size))
    return result


def delete_template(runtime: ContainerRuntime, name: str) -> None:
    """Delete a template image by short name."""
    runtime.remove_image(template_image_name(name))
