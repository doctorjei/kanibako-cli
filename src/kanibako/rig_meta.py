"""Truth-in-image metadata for *extended* rigs (``/etc/kanibako/rig.yaml``).

An extended rig is an interactively-built, non-reproducible image: there is no
external source to re-pull or rebuild from, so its identity must travel *inside*
the image itself. This module defines that in-image metadata file and its
serialization.

Because the metadata lives at a fixed path inside the rootfs, it rides
``podman save`` / ``load`` / ``push`` natively -- whoever ends up with the image
also ends up with its ``rig.yaml``, no side-channel required.

All on-disk serialization goes through PyYAML (``yaml.safe_load`` /
``yaml.safe_dump``); there is no hand-rolled serializer here.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import yaml  # type: ignore[import-untyped]


@dataclass
class RigMeta:
    """In-image metadata for an extended rig.

    ``name`` is the short rig name; ``parent`` and ``foundation_source`` are
    arbitrary image references / source descriptors (not validated here).
    ``recipe`` is an optional captured shell history of the steps that built
    the rig (informational only -- extended rigs are not reproducible).
    """

    name: str
    kind: str = "extended"
    parent: str | None = None
    foundation_source: str | None = None
    reproducible: bool = False
    created: str | None = None
    recipe: list[str] | None = None


# Field names in a stable, file-friendly order, used to filter unknown keys on
# load and to drive the ordered dump below.
_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(RigMeta))

# Fields that are always emitted, even when falsy (False / empty).
_ALWAYS: frozenset[str] = frozenset({"name", "kind", "reproducible"})


def dump_rig_meta(meta: RigMeta) -> str:
    """Serialize *meta* to a YAML string.

    ``name``, ``kind`` and ``reproducible`` are always emitted. The remaining
    fields are omitted when ``None`` so the file stays clean (in particular
    ``recipe`` disappears entirely when unset). Field order is stable and
    readable (``sort_keys=False``).
    """
    data: dict[str, object] = {}
    for field_name in _FIELD_NAMES:
        value = getattr(meta, field_name)
        if field_name not in _ALWAYS and value is None:
            continue
        data[field_name] = value
    return yaml.safe_dump(data, sort_keys=False)


def write_rig_meta(meta: RigMeta, path: Path) -> None:
    """Write *meta* to *path*, creating the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_rig_meta(meta))


def load_rig_meta(source: str | Path) -> RigMeta:
    """Load a :class:`RigMeta` from a file *path* or a raw YAML *string*.

    A :class:`~pathlib.Path` is read from disk; a ``str`` is parsed directly as
    YAML text. Unknown keys are ignored defensively (only known fields are
    passed to the constructor). Raises :class:`ValueError` if the document is
    empty/invalid or is missing the required ``name`` field.
    """
    text = source.read_text() if isinstance(source, Path) else source

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("rig.yaml must be a non-empty YAML mapping")

    kwargs = {k: v for k, v in data.items() if k in _FIELD_NAMES}
    if not kwargs.get("name"):
        raise ValueError("rig.yaml is missing the required 'name' field")

    return RigMeta(**kwargs)
