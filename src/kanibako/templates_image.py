"""Template image management: create, list, delete user templates."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.container import ContainerRuntime

_TEMPLATE_PREFIX = "kanibako-template-"
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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
