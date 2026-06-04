"""Pure rig-name resolution: classify a rig name into kind + prep action.

A *rig* is a container image a box can start from. This module answers the
question "given a name the user typed, what kind of rig is it and what (if
anything) must happen before it can be used?" -- WITHOUT performing any side
effects. It never pulls, builds, or commits; that is :func:`prep`'s job in a
later increment. Resolution only inspects the local image store, the bundled /
user-override template Containerfiles, and (eventually) the rig registry.

Kinds:
    ``"prefab"``    -- a published/base image, made ready by pulling.
    ``"template"``  -- a buildable ``Containerfile.template-<name>``.
    ``"extended"``  -- an interactively built, non-reproducible image.

Prep actions:
    ``"none"``     -- already prepped (the image exists locally).
    ``"pull"``     -- pull from a registry to prep.
    ``"build"``    -- build a Containerfile to prep.
    ``"missing"``  -- cannot be resolved (reserved; not produced yet).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.containerfiles import get_containerfile
from kanibako.templates_image import (
    list_bundled_templates,
    rig_image_name,
    template_image_name,
)

if TYPE_CHECKING:
    from kanibako.config import KanibakoConfig
    from kanibako.container import ContainerRuntime
    from kanibako.paths import StandardPaths


@dataclass(frozen=True)
class RigResolution:
    """The classification of a rig name.

    *kind* is one of ``"prefab"``, ``"template"``, ``"extended"``.
    *prep_action* is one of ``"pull"``, ``"build"``, ``"none"``, ``"missing"``.
    *image* is the OCI reference a prepped rig is (or will be) stored under.
    *containerfile* is set for buildable templates that need building.
    *source_ref* carries the original/source reference for prefabs (reserved).
    """

    name: str
    kind: str
    image: str
    prep_action: str
    containerfile: Path | None = None
    source_ref: str | None = None


def resolve_rig(
    name: str,
    runtime: ContainerRuntime,
    std: StandardPaths,
    merged: KanibakoConfig,
    *,
    registry: object | None = None,
) -> RigResolution:
    """Classify rig *name* into a :class:`RigResolution`. Pure -- no side effects.

    Precedence:

    1. **Already-prepped local image.** If a ``kanibako-template-<name>`` or
       ``kanibako-rig-<name>`` image exists locally, it is already prepped
       (``prep_action="none"``) with kind ``"template"`` / ``"extended"``.
    2. **Discovered template.** If *name* matches a bundled or user-override
       ``Containerfile.template-<name>``, it is a buildable template
       (``prep_action="build"``).
    3. **Resolvable reference / prefab.** Otherwise defer to
       :func:`resolve_image_reference`; the rig is a ``"prefab"`` made ready by
       ``"pull"`` (or ``"none"`` if the resolved image already exists locally).

    *registry* is accepted for forward compatibility (a later increment wires a
    durable locator registry); ``None`` skips that step.
    """
    # --- (a) Already-prepped local image -------------------------------
    # rig_image_name / template_image_name raise on names that aren't valid
    # short template names (e.g. anything with '/' or ':'); those can't be a
    # locally-prepped template/extended image, so just skip this step for them.
    try:
        extended_image = rig_image_name(name)
        template_image = template_image_name(name)
    except ValueError:
        extended_image = None
        template_image = None

    if template_image is not None and runtime.image_exists(template_image):
        return RigResolution(
            name=name,
            kind="template",
            image=template_image,
            prep_action="none",
        )
    if extended_image is not None and runtime.image_exists(extended_image):
        return RigResolution(
            name=name,
            kind="extended",
            image=extended_image,
            prep_action="none",
        )

    # --- (b) Discovered template (buildable Containerfile) -------------
    containers_dir = std.data_path / "containers"
    discovered = {t.name for t in list_bundled_templates(override_dir=containers_dir)}
    if name in discovered:
        containerfile = get_containerfile(f"template-{name}", containers_dir)
        return RigResolution(
            name=name,
            # template_image is non-None here: a discovered template name is a
            # valid short name, so template_image_name didn't raise above.
            kind="template",
            image=template_image or template_image_name(name),
            prep_action="build",
            containerfile=containerfile,
        )

    # --- (c) Known base / resolvable reference (prefab) ---------------
    # Imported lazily to avoid a circular import: commands.image imports
    # resolve_rig (from Increment 2 onward), and this module would otherwise
    # import commands.image at load time.
    from kanibako.commands.image import resolve_image_reference

    resolved = resolve_image_reference(name, runtime, merged.container_image)
    prep_action = "none" if runtime.image_exists(resolved) else "pull"
    return RigResolution(
        name=name,
        kind="prefab",
        image=resolved,
        prep_action=prep_action,
        source_ref=name,
    )
